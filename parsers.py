from __future__ import annotations

import io
import math
import re
import unicodedata
from datetime import date, datetime
from typing import BinaryIO

import pandas as pd
import pdfplumber
from pypdf import PdfReader

MONEY_RE = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}")

PLAN_LABELS = [
    ("À vista", "avista"), ("8x - 90%", "8x"), ("24x - 70%", "24x"),
    ("36x - 60%", "36x"), ("48x - 50%", "48x"), ("60x - 40%", "60x"),
    ("96x - 40%", "96x"),
]

CADASTRAL_LABEL_COLUMNS = ["IdFisico", "Compromissário / Responsável", "Crc", "Proprietário",
                           "Crc Proprietário", "Local do imóvel", "Bairro/Loteamento", "Q", "L"]

# Planos do REFIS 2026 (LC 1.230/2026, art. 2º): rótulo da coluna, percentual
# de desconto, quantidade nominal de parcelas, se o desconto incide sobre os
# juros de mora e se o plano respeita o valor mínimo de parcela (R$ 60,00).
# Pela planilha oficial, o desconto de juros (sobre o excedente SELIC − INPC) só
# vale até o plano de 24x; a partir de 36x desconta-se apenas a multa. O plano
# de 96x (CadÚnico) não aplica o mínimo legal de parcela.
REFIS_PLANS = [
    ("À vista", 1.00, 1, True, True),
    ("8x - 90%", 0.90, 8, True, True),
    ("24x - 70%", 0.70, 24, True, True),
    ("36x - 60%", 0.60, 36, False, True),
    ("48x - 50%", 0.50, 48, False, True),
    ("60x - 40%", 0.40, 60, False, True),
    ("96x - 40%", 0.40, 96, False, False),
]

# Valor mínimo legal da parcela (mantém o total ÷ nº de parcelas ≥ R$ 60,00).
MIN_PARCELA_REFIS = 60.0

# Plano de 96x (CadÚnico) dispensado do valor mínimo de parcela.
PLANS_SEM_MINIMO = {"96x"}

# SELIC acumulada e INPC acumulado por competência, conforme a planilha oficial
# "PLanos REFIS 2026 desconto SELIC.xlsx" (Planilha2). A competência referencia o
# vencimento da parcela: o desconto de juros incide sobre o excedente
# (SELIC - INPC)/SELIC apurado naquela competência.
SELIC_INPC = {
    "2018-01": (0.729100, 0.548100),
    "2018-02": (0.724400, 0.545300),
    "2018-03": (0.719100, 0.544300),
    "2018-04": (0.713900, 0.541000),
    "2018-05": (0.708700, 0.534400),
    "2018-06": (0.703500, 0.512800),
    "2018-07": (0.698100, 0.509000),
    "2018-08": (0.692400, 0.509000),
    "2018-09": (0.687700, 0.504500),
    "2018-10": (0.682300, 0.498500),
    "2018-11": (0.677400, 0.502300),
    "2018-12": (0.672500, 0.500200),
    "2019-01": (0.667100, 0.494800),
    "2019-02": (0.662200, 0.486800),
    "2019-03": (0.657500, 0.475400),
    "2019-04": (0.652300, 0.466600),
    "2019-05": (0.646900, 0.464400),
    "2019-06": (0.642200, 0.464300),
    "2019-07": (0.636500, 0.462800),
    "2019-08": (0.631500, 0.461000),
    "2019-09": (0.626900, 0.461800),
    "2019-10": (0.622100, 0.461200),
    "2019-11": (0.618300, 0.453300),
    "2019-12": (0.614600, 0.435800),
    "2020-01": (0.610800, 0.433100),
    "2020-02": (0.607900, 0.430700),
    "2020-03": (0.604500, 0.428100),
    "2020-04": (0.601700, 0.431400),
    "2020-05": (0.599300, 0.435000),
    "2020-06": (0.597200, 0.430700),
    "2020-07": (0.595300, 0.424400),
    "2020-08": (0.593700, 0.419300),
    "2020-09": (0.592100, 0.407100),
    "2020-10": (0.590500, 0.394600),
    "2020-11": (0.589000, 0.381500),
    "2020-12": (0.587400, 0.361600),
    "2021-01": (0.585900, 0.358000),
    "2021-02": (0.584600, 0.346900),
    "2021-03": (0.582600, 0.335400),
    "2021-04": (0.580500, 0.330400),
    "2021-05": (0.577800, 0.317700),
    "2021-06": (0.574700, 0.309900),
    "2021-07": (0.571100, 0.296700),
    "2021-08": (0.566800, 0.285300),
    "2021-09": (0.562400, 0.270100),
    "2021-10": (0.557500, 0.255500),
    "2021-11": (0.551600, 0.245100),
    "2021-12": (0.543900, 0.236100),
    "2022-01": (0.536600, 0.227800),
    "2022-02": (0.529000, 0.215700),
    "2022-03": (0.519700, 0.195200),
    "2022-04": (0.511400, 0.182900),
    "2022-05": (0.501100, 0.177600),
    "2022-06": (0.490900, 0.170400),
    "2022-07": (0.480600, 0.177400),
    "2022-08": (0.469000, 0.181100),
    "2022-09": (0.458300, 0.184900),
    "2022-10": (0.448100, 0.179400),
    "2022-11": (0.437900, 0.174900),
    "2022-12": (0.426700, 0.166800),
    "2023-01": (0.415500, 0.161500),
    "2023-02": (0.406300, 0.152600),
    "2023-03": (0.394600, 0.145300),
    "2023-04": (0.385400, 0.139300),
    "2023-05": (0.374200, 0.135200),
    "2023-06": (0.363500, 0.136300),
    "2023-07": (0.352800, 0.137300),
    "2023-08": (0.341500, 0.135100),
    "2023-09": (0.331800, 0.133800),
    "2023-10": (0.321800, 0.132400),
    "2023-11": (0.312600, 0.131300),
    "2023-12": (0.303700, 0.125100),
    "2024-01": (0.294000, 0.118800),
    "2024-02": (0.286000, 0.109800),
    "2024-03": (0.277700, 0.107700),
    "2024-04": (0.268800, 0.103600),
    "2024-05": (0.260500, 0.098500),
    "2024-06": (0.252600, 0.095800),
    "2024-07": (0.243500, 0.092900),
    "2024-08": (0.234800, 0.094500),
    "2024-09": (0.226400, 0.089200),
    "2024-10": (0.217100, 0.082600),
    "2024-11": (0.209200, 0.079100),
    "2024-12": (0.199900, 0.073900),
    "2025-01": (0.189800, 0.073900),
    "2025-02": (0.180000, 0.058300),
    "2025-03": (0.170400, 0.052900),
    "2025-04": (0.159800, 0.047900),
    "2025-05": (0.148400, 0.044200),
    "2025-06": (0.137400, 0.041800),
    "2025-07": (0.124600, 0.039600),
    "2025-08": (0.113000, 0.041800),
    "2025-09": (0.100800, 0.036400),
    "2025-10": (0.088000, 0.036100),
    "2025-11": (0.077500, 0.035800),
    "2025-12": (0.065300, 0.033600),
}

_SELIC_INPC_DEFAULT_MES = max(SELIC_INPC)


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).strip().lower()


def br_money(value: object) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if not text:
        return 0.0
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return float(text)


def br_currency(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def read_pdf_text(file: BinaryIO) -> str:
    file.seek(0)
    reader = PdfReader(file)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _field(text: str, pattern: str, default: str = "") -> str:
    match = re.search(pattern, text, re.I | re.M)
    return " ".join(match.group(1).split()).strip() if match else default


def _plan_line(text: str, kind: str) -> tuple[float | None, int | None, float | None]:
    tests = {
        "normal": r"^.*sem desconto.*$",
        "avista": r"^.*\bA\s+vista\b.*$",
        "8x": r"^.*\b8x\s*-\s*90%.*$",
        "24x": r"^.*\b24x\s*-\s*70%.*$",
        "36x": r"^.*\b36x\s*-\s*60%.*$",
        "48x": r"^.*\b48x\s*-\s*50%.*$",
        "60x": r"^.*\b60x\s*-\s*40%.*$",
        "96x": r"^.*\b96x\s*-\s*40%.*Cad.*(?:Unico|Único).*$",
    }
    match = re.search(tests[kind], text, re.I | re.M)
    if not match:
        return None, None, None
    line = match.group(0)
    amounts = MONEY_RE.findall(line)
    tail = re.search(r"\s(\d+)\s+(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})\s*$", line)
    if len(amounts) < 2 or not tail:
        return None, None, None
    return br_money(amounts[-2]), int(tail.group(1)), br_money(tail.group(2))


def parse_previsao_pdf(file: BinaryIO, filename: str = "") -> dict:
    text = read_pdf_text(file)
    id_fisico = _field(text, r"\bIdFisico\s+(\d+)")
    if not id_fisico:
        id_fisico = _field(text, r"Origem da Pesquisa:\s*Imobiliario\s+(\d+)")

    holder = re.search(
        r"^(Compromissário|Responsável)\s+(.+?)\s+-\s+Crc\s+(\d+)", text, re.I | re.M
    )
    owner = re.search(r"^Proprietário\s+(.+?)\s+-\s+Crc\s+(\d+)", text, re.I | re.M)
    local = _field(text, r"^Local do Imóvel\s+(.+)$")
    neighborhood_line = _field(text, r"^Bairro/Loteamento\s+(.+)$")
    neighborhood, quadra, lote = neighborhood_line, "", ""
    ql = re.search(r"^(.*?)\s+Q:\s*(.*?)\s+L:\s*(.+?)\s*$", neighborhood_line)
    if ql:
        neighborhood, quadra, lote = (" ".join(g.split()) for g in ql.groups())

    parts = text.split("Planos de Parcelamentos", 1)
    debt_part = parts[0]
    plans_text = parts[1] if len(parts) > 1 else text
    years = [int(y) for y in re.findall(r"^(?:CUSTAS|IPTU(?:/TSU)?|IPTU\s*/\s*CIP|CM\s+\S.*?)\s+(\d{4})\b", debt_part, re.I | re.M)]
    exercise = f"{min(years)} a {max(years)}" if years else ""

    normal_total, _, _ = _plan_line(plans_text, "normal")
    plans = {}
    for key in ("avista", "8x", "24x", "36x", "48x", "60x", "96x"):
        total, count, installment = _plan_line(plans_text, key)
        plans[key] = {"total": total, "count": count, "installment": installment}

    return {
        "Arquivo": filename,
        "IdFisico": int(id_fisico) if id_fisico else "",
        "Compromissário / Responsável": " ".join(holder.group(2).split()) if holder else "",
        "Crc": int(holder.group(3)) if holder else "",
        "Proprietário": " ".join(owner.group(1).split()) if owner else "",
        "Crc Proprietário": int(owner.group(2)) if owner else "",
        "Local do imóvel": local,
        "Bairro/Loteamento": neighborhood,
        "Q": quadra,
        "L": lote,
        "Exercício": exercise,
        "Normal": normal_total,
        "_plans": plans,
    }


def apply_installment_columns(row: dict, minimum: float = 60.0) -> dict:
    """Preenche as colunas dos planos com o VALOR DA PARCELA (numérico),
    exatamente como aparece no PDF.

    Todos os planos aparecem sempre (sem regra de ocultação). Planos com
    parcela abaixo do mínimo são registrados em ``row["_flags"]`` para
    destaque em vermelho e aviso.
    """
    flags: dict[str, str] = {}
    for label, key in PLAN_LABELS:
        plan = row["_plans"].get(key, {})
        value, total = plan.get("installment"), plan.get("total")
        if isinstance(value, (int, float)):
            row[label] = float(value)
            if (key not in ("avista", "96x") and minimum > 0
                    and value < minimum and isinstance(total, (int, float))):
                max_n = math.floor(float(total) / minimum)
                if max_n >= 1:
                    flags[label] = (
                        f"Parcela de {br_currency(value)} está abaixo do mínimo de "
                        f"{br_currency(minimum)}; mantendo o mínimo, o máximo é de "
                        f"{max_n} parcela{'s' if max_n != 1 else ''}."
                    )
                else:
                    flags[label] = (
                        f"Parcela de {br_currency(value)} está abaixo do mínimo de "
                        f"{br_currency(minimum)}; não é possível parcelar mantendo esse mínimo."
                    )
        else:
            row[label] = None
    row["_flags"] = flags
    return row


def new_plan_totals() -> dict[str, float]:
    """Acumulador zerado com Normal e todos os planos."""
    return {"Normal": 0.0, **{key: 0.0 for _, key in PLAN_LABELS}}


def accumulate_plan_totals(totals: dict[str, float], row: dict) -> None:
    """Soma os valores das parcelas de cada plano de uma linha processada.

    A linha TOTAL soma exatamente o que aparece nas células (=SOMA bate).
    """
    if isinstance(row.get("Normal"), (int, float)):
        totals["Normal"] += float(row["Normal"])
    for _, key in PLAN_LABELS:
        plan = row.get("_plans", {}).get(key) or {}
        if isinstance(plan.get("installment"), (int, float)):
            totals[key] += float(plan["installment"])


def build_total_row(totals: dict[str, float], columns: list[str]) -> dict:
    """Última linha da exportação: soma dos valores das parcelas de cada
    plano entre todos os cadastros (bate com a =SOMA() das colunas)."""
    plan_columns = {label for label, _ in PLAN_LABELS} | {"Normal"}
    label_col = next((c for c in CADASTRAL_LABEL_COLUMNS if c in columns), None)
    if label_col is None:
        label_col = next((c for c in columns if c not in plan_columns), None)
    row = {c: "" for c in columns}
    if label_col:
        row[label_col] = "TOTAL"
    if "Normal" in row:
        row["Normal"] = totals.get("Normal", 0.0)
    for label, key in PLAN_LABELS:
        if label in row:
            row[label] = totals.get(key, 0.0)
    return row


def _vencimento_to_mes(value: object) -> str:
    """Converte um vencimento em 'YYYY-MM' para consultar a tabela SELIC/INPC."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m")
    try:
        return pd.to_datetime(value, dayfirst=True, errors="raise").strftime("%Y-%m")
    except (ValueError, TypeError):
        return ""


def _lookup_selic_inpc(mes: str) -> tuple[float, float]:
    return SELIC_INPC.get(mes, (0.0, 0.0))


def _parcela_descontada(total: float, juros: float, multa: float, selic: float,
                        inpc: float, percentual: float, desconta_juros: bool) -> float:
    """Valor a pagar de um débito após o desconto do plano REFIS.

    O desconto incide sobre a multa de mora (percentual do plano) e, apenas nos
    planos de até 24x, sobre os juros acima da inflação: a parcela descontada dos
    juros é limitada ao excedente (SELIC - INPC)/SELIC apurado na competência do
    vencimento. Original, correção monetária e honorários nunca entram no
    desconto. Para planos de 36x em diante os juros entram pelo valor cheio.
    """
    desconto_juros = percentual * juros * max(0.0, (selic - inpc) / selic) if desconta_juros and selic > 0 else 0.0
    desconto_multa = percentual * multa
    return max(0.0, total - desconto_juros - desconto_multa)


def read_debt_pdf(file: BinaryIO) -> pd.DataFrame:
    file.seek(0)
    rows = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for values in table:
                    if len(values) >= 12 and str(values[1] or "").strip().isdigit():
                        rows.append({
                            "Exercício": values[1], "Situação": values[3],
                            "Vencimento": values[4], "Total": values[5], "Origem": values[7],
                            "Original": values[8] if len(values) > 8 else "",
                            "Correção": values[9] if len(values) > 9 else "",
                            "Juros": values[10] if len(values) > 10 else "",
                            "Multa": values[11] if len(values) > 11 else "",
                            "Honorários": values[12] if len(values) > 12 else "",
                        })
    if not rows:
        raise ValueError("Não foi possível localizar a tabela de débitos no PDF.")
    return pd.DataFrame(rows)


def read_sheet(file: BinaryIO, filename: str) -> pd.DataFrame:
    file.seek(0)
    lower = filename.lower()
    if lower.endswith(".csv"):
        raw = file.read()
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(io.BytesIO(raw), sep=None, engine="python", encoding=encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("Não foi possível identificar a codificação do CSV.")
    # As planilhas exportadas trazem linhas de título antes do cabeçalho real.
    # Localiza a linha de cabeçalho procurando por nomes de colunas conhecidos.
    df = pd.read_excel(file, header=None)
    header_idx = 0
    for i in range(len(df)):
        non_empty = [v for v in df.iloc[i].tolist()
                     if v is not None and not (isinstance(v, float) and pd.isna(v))
                     and str(v).strip() != ""]
        if len(non_empty) >= 3:
            header_idx = i
            break
    df.columns = [str(c).strip() for c in df.iloc[header_idx].tolist()]
    df = df.iloc[header_idx + 1:].reset_index(drop=True)
    df = df.loc[:, [c for c in df.columns if c and str(c) not in ("nan", "None") and not str(c).startswith("Unnamed")]]
    return df.dropna(how="all").reset_index(drop=True)


def canonical_debts(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "Exercício": ["exercicio", "ano"], "Origem": ["origem", "idfisico", "fisico"],
        "Total": ["total", "subtotal", "valor"], "Situação": ["situacao", "situacao parcela"],
        "Vencimento": ["vencimento", "data vencimento"],
        "Original": ["original", "principal"],
        "Juros": ["juros", "juros de mora"],
        "Multa": ["multa", "multa de mora"],
        "Correção": ["correcao", "correcao monetaria"],
        "Honorários": ["honorarios", "honorarios advocaticios"],
    }
    normalized = {normalize(c): c for c in df.columns}
    rename = {}
    for target, names in aliases.items():
        for name in names:
            if name in normalized:
                rename[normalized[name]] = target
                break
    result = df.rename(columns=rename).copy()
    missing = [c for c in ("Exercício", "Origem", "Total") if c not in result]
    if missing:
        raise ValueError("Colunas obrigatórias ausentes: " + ", ".join(missing))
    return result


def read_properties_pdf(file: BinaryIO) -> pd.DataFrame:
    file.seek(0)
    all_rows = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table or not table[0]:
                    continue
                headers = [str(x or "").replace("\n", " ").strip() for x in table[0]]
                if any(normalize(h) == "fisico" for h in headers):
                    for row in table[1:]:
                        if row and row[0]:
                            all_rows.append(dict(zip(headers, row)))
    if not all_rows:
        raise ValueError("Não foi possível localizar a tabela de imóveis no PDF.")
    return canonical_properties(pd.DataFrame(all_rows))


def canonical_properties(df: pd.DataFrame) -> pd.DataFrame:
    alias_groups = {
        "IdFisico": ["fisico", "idfisico", "id fisico", "origem"],
        "Proprietário": ["proprietario"], "Compromissário / Responsável": ["responsavel", "compromissario", "compromissario / responsavel"],
        "Logradouro": ["imovel logradouro", "logradouro", "local do imovel"],
        "Número": ["imovel numero", "numero"], "Q": ["quadra", "q"], "L": ["lote", "l"],
        "Crc": ["crc"], "Crc Proprietário": ["crc proprietario"], "Bairro/Loteamento": ["bairro/loteamento", "bairro", "loteamento"],
    }
    normalized = {normalize(c): c for c in df.columns}
    rename = {}
    for target, aliases in alias_groups.items():
        for alias in aliases:
            if alias in normalized:
                rename[normalized[alias]] = target
                break
    result = df.rename(columns=rename).copy()
    if "IdFisico" not in result:
        raise ValueError("A coluna Físico/IdFisico não foi encontrada no cadastro de imóveis.")
    for col in alias_groups:
        if col not in result:
            result[col] = ""
    for col in ("Q", "L", "Crc", "Crc Proprietário", "Bairro/Loteamento"):
        result[col] = result[col].fillna("").astype(str).str.strip().replace({".": "", "nan": ""})
    result["Local do imóvel"] = (
        result["Logradouro"].fillna("").astype(str).str.strip() + " " + result["Número"].fillna("").astype(str).str.strip()
    ).str.strip()
    return result


def build_module2(debts: pd.DataFrame, properties: pd.DataFrame, today: date | None = None) -> tuple[pd.DataFrame, list[str]]:
    today = today or date.today()
    debts = canonical_debts(debts)
    properties = canonical_properties(properties)
    warnings = []
    debts["Origem_key"] = debts["Origem"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    debts["Exercício_num"] = pd.to_numeric(debts["Exercício"], errors="coerce")

    # Colunas monetárias: Total é obrigatória; as demais são opcionais e, quando
    # ausentes, valem 0 (sem juros/multa a descontar).
    for col in ("Total", "Juros", "Multa", "Original", "Correção", "Honorários"):
        debts[col + "_num"] = debts[col].map(br_money) if col in debts else 0.0

    if "Situação" in debts:
        is_normal = debts["Situação"].map(normalize).eq("normal")
    else:
        is_normal = debts["Exercício_num"].eq(today.year)
        warnings.append("A planilha de débitos não contém Situação; linhas do ano atual foram tratadas como normais.")

    old = debts[~is_normal & debts["Exercício_num"].notna()]

    # Agregação por imóvel: "Normal" soma os totais e, para cada plano, soma-se o
    # valor já descontado. O desconto é calculado DÉBITO a DÉBITO, pois cada um tem
    # os seus próprios juros/multa e competência de vencimento (que define a SELIC
    # e o INPC usados no excedente). Débitos do ano corrente (normal) não recebem
    # desconto, entrando pelo valor cheio.
    plan_labels = [label for label, _, _, _, _ in REFIS_PLANS]
    agg: dict[str, dict] = {}
    for _, r in old.iterrows():
        key = r["Origem_key"]
        if key not in agg:
            agg[key] = {"AnoInicial": None, "AnoFinal": None, "Normal": 0.0,
                        "plans": {label: 0.0 for label in plan_labels}}
        item = agg[key]
        exercicio = int(r["Exercício_num"])
        if item["AnoInicial"] is None or exercicio < item["AnoInicial"]:
            item["AnoInicial"] = exercicio
        if item["AnoFinal"] is None or exercicio > item["AnoFinal"]:
            item["AnoFinal"] = exercicio
        total = float(r["Total_num"])
        item["Normal"] += total
        if exercicio < today.year:
            juros = float(r["Juros_num"])
            multa = float(r["Multa_num"])
            mes = _vencimento_to_mes(r.get("Vencimento")) or _SELIC_INPC_DEFAULT_MES
            selic, inpc = _lookup_selic_inpc(mes)
            for label, pct, _, desconta_juros, _ in REFIS_PLANS:
                item["plans"][label] += _parcela_descontada(total, juros, multa, selic, inpc, pct, desconta_juros)
        else:
            for label, _, _, _, _ in REFIS_PLANS:
                item["plans"][label] += total

    grouped = pd.DataFrame([
        {
            "Origem_key": key,
            "AnoInicial": v["AnoInicial"],
            "AnoFinal": v["AnoFinal"],
            "Normal": v["Normal"],
            **{label: v["plans"][label] for label in plan_labels},
        }
        for key, v in agg.items()
    ], columns=["Origem_key", "AnoInicial", "AnoFinal", "Normal"] + plan_labels)

    overdue = pd.Series(dtype="int64")
    if "Vencimento" in debts:
        due = pd.to_datetime(debts["Vencimento"], dayfirst=True, errors="coerce")
        mask = is_normal & debts["Exercício_num"].eq(today.year) & due.dt.date.map(lambda d: bool(d and d < today))
        overdue = debts[mask].groupby("Origem_key").size()
    else:
        warnings.append("Sem coluna Vencimento, não foi possível contar parcelas vencidas do ano atual.")

    props = properties.copy()
    props["Origem_key"] = props["IdFisico"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    merged = props.merge(grouped, on="Origem_key", how="left")
    merged["Exercício"] = merged.apply(
        lambda r: f"{int(r.AnoInicial)} a {int(r.AnoFinal)}" if pd.notna(r.AnoInicial) else "", axis=1
    )
    counts = merged["Origem_key"].map(overdue).fillna(0).astype(int)
    year_col = str(today.year)
    merged[year_col] = counts.map(lambda n: "" if n == 0 else ("1 parcela vencida" if n == 1 else f"{n} parcelas vencidas"))
    merged["Normal"] = merged["Normal"].fillna(0.0)

    # Colunas de REFIS: valor da parcela já com desconto (total descontado ÷ nº de
    # parcelas). Quando o valor nominal fica abaixo do mínimo legal (R$ 60,00), a
    # quantidade de parcelas é reduzida para manter a parcela no mínimo — como o
    # sistema oficial — exceto no plano de 96x (CadÚnico), que não aplica o mínimo.
    for label, _, n, _, aplica_min in REFIS_PLANS:
        total_plan = merged[label].fillna(0.0)
        if n <= 0:
            merged[label] = 0.0
            continue
        nominal = total_plan / n
        if aplica_min:
            below = (total_plan > 0) & (nominal < MIN_PARCELA_REFIS)
            n_eff = (total_plan / MIN_PARCELA_REFIS).astype(int).clip(lower=1)
            merged[label] = (total_plan / n_eff.where(below, n)).round(2)
        else:
            merged[label] = nominal.round(2)

    output_cols = ["IdFisico", "Compromissário / Responsável", "Crc", "Proprietário", "Crc Proprietário",
                   "Local do imóvel", "Bairro/Loteamento", "Q", "L", "Exercício", year_col, "Normal",
                   "À vista", "8x - 90%", "24x - 70%", "36x - 60%", "48x - 50%", "60x - 40%", "96x - 40%"]
    return merged[output_cols], warnings
