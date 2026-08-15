from __future__ import annotations

import io
import re
import unicodedata
from datetime import date, datetime
from typing import BinaryIO

import pandas as pd
import pdfplumber
from pypdf import PdfReader

MONEY_RE = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}")


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
        "avista": r"^.*A vista.*$",
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

    debt_part = text.split("Planos de Parcelamentos", 1)[0]
    years = [int(y) for y in re.findall(r"^(?:CUSTAS|IPTU(?:/TSU)?|IPTU\s*/\s*CIP|CM\s+\S.*?)\s+(\d{4})\b", debt_part, re.I | re.M)]
    exercise = f"{min(years)} a {max(years)}" if years else ""

    normal_total, _, _ = _plan_line(text, "normal")
    plans = {}
    for key in ("avista", "8x", "24x", "36x", "48x", "60x"):
        total, count, installment = _plan_line(text, key)
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
    labels = [
        ("À vista", "avista"), ("8x - 90%", "8x"), ("24x - 70%", "24x"),
        ("36x - 60%", "36x"), ("48x - 50%", "48x"), ("60x - 40%", "60x"),
    ]
    blocked = False
    for label, key in labels:
        plan = row["_plans"].get(key, {})
        count, value = plan.get("count"), plan.get("installment")
        if blocked or value is None:
            row[label] = ""
        elif key != "avista" and value < minimum:
            blocked = True
            row[label] = ""
        else:
            row[label] = f"{count}x de R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    row.pop("_plans", None)
    return row


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
    return pd.read_excel(file)


def canonical_debts(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "Exercício": ["exercicio", "ano"], "Origem": ["origem", "idfisico", "fisico"],
        "Total": ["subtotal", "total", "valor"], "Situação": ["situacao", "situacao parcela"],
        "Vencimento": ["vencimento", "data vencimento"],
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
    debts["Total_num"] = debts["Total"].map(br_money)
    if "Situação" in debts:
        is_normal = debts["Situação"].map(normalize).eq("normal")
    else:
        is_normal = debts["Exercício_num"].eq(today.year)
        warnings.append("A planilha de débitos não contém Situação; linhas do ano atual foram tratadas como normais.")
    old = debts[~is_normal & debts["Exercício_num"].notna()]
    grouped = old.groupby("Origem_key").agg(
        AnoInicial=("Exercício_num", "min"), AnoFinal=("Exercício_num", "max"), Normal=("Total_num", "sum")
    )
    overdue = pd.Series(dtype="int64")
    if "Vencimento" in debts:
        due = pd.to_datetime(debts["Vencimento"], dayfirst=True, errors="coerce")
        mask = is_normal & debts["Exercício_num"].eq(today.year) & due.dt.date.map(lambda d: bool(d and d < today))
        overdue = debts[mask].groupby("Origem_key").size()
    else:
        warnings.append("Sem coluna Vencimento, não foi possível contar parcelas vencidas do ano atual.")

    props = properties.copy()
    props["Origem_key"] = props["IdFisico"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    merged = props.merge(grouped, left_on="Origem_key", right_index=True, how="left")
    merged["Exercício"] = merged.apply(
        lambda r: f"{int(r.AnoInicial)} a {int(r.AnoFinal)}" if pd.notna(r.AnoInicial) else "", axis=1
    )
    counts = merged["Origem_key"].map(overdue).fillna(0).astype(int)
    year_col = str(today.year)
    merged[year_col] = counts.map(lambda n: "" if n == 0 else ("1 parcela vencida" if n == 1 else f"{n} parcelas vencidas"))
    merged["Normal"] = merged["Normal"].fillna(0.0)
    for col in ["À vista", "8x - 90%", "24x - 70%", "36x - 60%", "48x - 50%", "60x - 40%"]:
        merged[col] = ""
    output_cols = ["IdFisico", "Compromissário / Responsável", "Crc", "Proprietário", "Crc Proprietário",
                   "Local do imóvel", "Bairro/Loteamento", "Q", "L", "Exercício", year_col, "Normal",
                   "À vista", "8x - 90%", "24x - 70%", "36x - 60%", "48x - 50%", "60x - 40%"]
    return merged[output_cols], warnings
