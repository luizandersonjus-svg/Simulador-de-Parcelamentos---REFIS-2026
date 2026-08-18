from __future__ import annotations
import io
from datetime import date
import pandas as pd
import streamlit as st

from excel_utils import dataframe_to_xlsx
from parsers import (
    accumulate_plan_totals, apply_installment_columns, build_module2,
    build_total_row, new_plan_totals, parse_previsao_pdf,
    read_debt_pdf, read_properties_pdf, read_sheet,
)

st.set_page_config(page_title="Conversor de documentos", page_icon="📄", layout="wide")
st.title("📄 Conversor de documentos para Excel")
st.caption("Processamento temporário: os arquivos não são armazenados em banco de dados.")

MODULE1_COLUMNS = ["IdFisico", "Compromissário / Responsável", "Crc", "Proprietário", "Crc Proprietário",
                   "Local do imóvel", "Bairro/Loteamento", "Q", "L", "Exercício", "Normal", "À vista",
                   "8x - 90%", "24x - 70%", "36x - 60%", "48x - 50%", "60x - 40%"]

module = st.sidebar.radio("Escolha o módulo", ["1 — Previsão de parcelamento", "2 — Cruzamento de débitos e imóveis"])
st.sidebar.info("Antes de exportar, confira a prévia e eventuais avisos.")

if module.startswith("1"):
    st.header("Módulo 1 — PDFs de previsão de parcelamento")
    st.write("Envie até 100 PDFs do modelo validado. Cada documento gerará uma linha.")
    files = st.file_uploader("PDFs", type=["pdf"], accept_multiple_files=True)
    col_a, col_b = st.columns([2, 1])
    with col_a:
        selected = st.multiselect("Colunas da planilha", MODULE1_COLUMNS, default=MODULE1_COLUMNS)
    with col_b:
        minimum = st.number_input("Valor mínimo da parcela (R$)", min_value=0.0, value=60.0, step=1.0)
        st.caption("Parcelas abaixo desse valor ficam destacadas em vermelho, com aviso.")
    if len(files) > 100:
        st.error("O limite é de 100 PDFs por processamento.")
    elif files and st.button("Processar PDFs", type="primary"):
        rows, errors, flags = [], [], []
        totals = new_plan_totals()
        progress = st.progress(0)
        for i, file in enumerate(files):
            try:
                row = parse_previsao_pdf(io.BytesIO(file.getvalue()), file.name)
                row = apply_installment_columns(row, minimum)
                accumulate_plan_totals(totals, row)
                for label, message in row.get("_flags", {}).items():
                    flags.append({"row": len(rows), "column": label, "message": message,
                                  "arquivo": file.name, "idfisico": row.get("IdFisico", "")})
                row.pop("_flags", None)
                row.pop("_plans", None)
                rows.append(row)
            except Exception as exc:
                errors.append({"Arquivo": file.name, "Erro": str(exc)})
            progress.progress((i + 1) / len(files))
        st.session_state["module1_result"] = pd.DataFrame(rows)
        st.session_state["module1_errors"] = errors
        st.session_state["module1_totals"] = totals
        st.session_state["module1_flags"] = flags

    if "module1_result" in st.session_state:
        df = st.session_state["module1_result"]
        visible = [c for c in selected if c in df.columns]
        result = df[visible] if visible else df.iloc[:, 0:0]
        flags = st.session_state.get("module1_flags", [])
        visible_flags = [f for f in flags if f["column"] in result.columns]
        if visible and not df.empty and "module1_totals" in st.session_state:
            total_row = build_total_row(st.session_state["module1_totals"], list(result.columns))
            result = pd.concat([result, pd.DataFrame([total_row])], ignore_index=True)
            st.caption("Linha TOTAL: soma os valores das parcelas exibidas em cada coluna.")
        st.subheader("Prévia")
        if visible_flags and not result.empty:
            mask = pd.DataFrame(False, index=result.index, columns=result.columns)
            for f in visible_flags:
                if f["row"] < len(df):
                    mask.loc[f["row"], f["column"]] = True

            def _flag_style(frame):
                styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
                m = mask.reindex(index=frame.index, columns=frame.columns).fillna(False)
                return styles.where(~m, "background-color: #ffc7ce; color: #9c0006; font-weight: bold")

            st.dataframe(result.style.apply(_flag_style, axis=None), use_container_width=True, hide_index=True)
            with st.expander(f"🔔 {len(visible_flags)} aviso(s) — parcela abaixo do mínimo (células em vermelho)"):
                for f in visible_flags:
                    st.markdown(f"- **IdFisico {f['idfisico']}** ({f['arquivo']}) — **{f['column']}**: {f['message']}")
        else:
            st.dataframe(result, use_container_width=True, hide_index=True)
        if st.session_state.get("module1_errors"):
            st.warning(f"{len(st.session_state['module1_errors'])} arquivo(s) apresentaram erro.")
            st.dataframe(pd.DataFrame(st.session_state["module1_errors"]), hide_index=True)
        if not result.empty:
            st.download_button("⬇️ Baixar Excel",
                               dataframe_to_xlsx(result, "Previsões", flags=visible_flags),
                               "previsoes_parcelamento.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

else:
    st.header("Módulo 2 — Cruzamento de débitos e imóveis")
    st.write("O sistema relaciona **Origem** do primeiro documento com **Físico/IdFisico** do segundo.")
    left, right = st.columns(2)
    with left:
        debt_file = st.file_uploader("1. Documento de débitos", type=["pdf", "xlsx", "xls", "csv"], key="debts")
        st.caption("Obrigatórios: Exercício, Origem e Total/Subtotal. Situação e Vencimento permitem tratar o ano atual.")
    with right:
        property_file = st.file_uploader("2. Cadastro de imóveis", type=["pdf", "xlsx", "xls", "csv"], key="properties")
        st.caption("Obrigatório: Físico/IdFisico. Os demais campos são preenchidos quando disponíveis.")
    processing_date = st.date_input("Data de referência para parcelas vencidas", value=date.today(), format="DD/MM/YYYY")

    if debt_file and property_file and st.button("Cruzar documentos", type="primary"):
        try:
            if debt_file.name.lower().endswith(".pdf"):
                debts = read_debt_pdf(io.BytesIO(debt_file.getvalue()))
            else:
                debts = read_sheet(io.BytesIO(debt_file.getvalue()), debt_file.name)
            if property_file.name.lower().endswith(".pdf"):
                properties = read_properties_pdf(io.BytesIO(property_file.getvalue()))
            else:
                properties = read_sheet(io.BytesIO(property_file.getvalue()), property_file.name)
            result, warnings = build_module2(debts, properties, processing_date)
            st.session_state["module2_result"] = result
            st.session_state["module2_warnings"] = warnings
        except Exception as exc:
            st.error(f"Não foi possível processar os documentos: {exc}")

    if "module2_result" in st.session_state:
        result = st.session_state["module2_result"]
        for warning in st.session_state.get("module2_warnings", []):
            st.warning(warning)
        st.subheader("Prévia")
        st.dataframe(result, use_container_width=True, hide_index=True)
        st.download_button("⬇️ Baixar Excel", dataframe_to_xlsx(result, "Resultado"),
                           "cruzamento_debitos_imoveis.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.info("As colunas de REFIS mostram o valor da parcela com o desconto legal do plano aplicado. "
                "A multa de mora recebe o desconto do plano em todas as opções; os juros são descontados "
                "apenas até o plano de 24x e somente sobre o excedente (SELIC − INPC) da competência do "
                "vencimento de cada parcela.")
