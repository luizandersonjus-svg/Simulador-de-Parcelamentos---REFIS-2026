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
]

CADASTRAL_LABEL_COLUMNS = ["IdFisico", "Compromissário / Responsável", "Crc", "Proprietário",
                           "Crc Proprietário", "Local do imóvel", "Bairro/Loteamento", "Q", "L"]

# Planos do REFIS 2026 (LC 1.230/2026, art. 2º): rótulo da coluna, percentual de
# desconto e quantidade de parcelas.
REFIS_PLANS = [
    ("À vista", 1.00, 1),
    ("8x - 90%", 0.90, 8),
    ("24x - 70%", 0.70, 24),
    ("36x - 60%", 0.60, 36),
    ("48x - 50%", 0.50, 48),
    ("60x - 40%", 0.40, 60),
]


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
    for key in ("avista", "8x", "24x", "36x", "48x", "60x"):
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
            if (key != "avista" and minimum > 0
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


def _parcela_descontada(total: float, juros: float, multa: float, percentual: float) -> float:
    """Valor a pagar de um débito após o desconto do plano.

    O desconto incide sobre a somatória de juros de mora + multa na proporção do
    plano (À vista 100%, 8x-90%, ...). Original, correção monetária e honorários
    nunca entram no desconto, pois já compõem o Total como parcelas não
    descontáveis — o Total é reduzido apenas pela parcela descontada.
    """
    desconto = percentual * (juros + multa)
    return max(0.0, total - desconto)


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
    # os seus próprios juros/multa. Débitos do ano corrente (normal) não recebem
    # desconto, entrando pelo valor cheio.
    plan_labels = [label for label, _, _ in REFIS_PLANS]
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
            for label, pct, _ in REFIS_PLANS:
                item["plans"][label] += _parcela_descontada(total, juros, multa, pct)
        else:
            for label, _, _ in REFIS_PLANS:
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

    # Colunas de REFIS: valor da parcela já com desconto (total descontado ÷ nº de parcelas).
    for label, _, n in REFIS_PLANS:
        merged[label] = (merged[label].fillna(0.0) / n).round(2)

    output_cols = ["IdFisico", "Compromissário / Responsável", "Crc", "Proprietário", "Crc Proprietário",
                   "Local do imóvel", "Bairro/Loteamento", "Q", "L", "Exercício", year_col, "Normal",
                   "À vista", "8x - 90%", "24x - 70%", "36x - 60%", "48x - 50%", "60x - 40%"]
    return merged[output_cols], warnings
