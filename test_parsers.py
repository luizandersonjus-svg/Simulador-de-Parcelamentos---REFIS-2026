"""Testes do parser de Previsão de Parcelamento (Módulo 1).

Usa apenas textos sintéticos, sem dados reais.
"""
import re

import pandas as pd
import pytest

from parsers import (
    _plan_line, accumulate_plan_totals, apply_installment_columns,
    build_module2_total_row, build_total_row, new_plan_totals,
)

PLANS_SECTION = """Planos de Parcelamentos Original Correção Juros Multa Honorários Total Parcelas Vlr Parcela
REFIS 2026 - sem desconto Bloq Jud 1.686,76 4.051,60 17.025,56 573,80 931,41 24.286,38 1 24.286,38
REFIS 2026 - A vista 100% 1.686,76 4.051,60 0,00 0,00 931,41 6.687,02 1 6.687,02
REFIS 2026 - 8x - 90% 1.686,76 4.051,60 1.702,55 57,21 931,41 8.446,78 8 1.055,85
REFIS 2026 - 24x - 70% 1.686,76 4.051,60 5.107,66 172,05 931,41 11.966,73 24 498,62
REFIS 2026 - 36x - 60% 1.686,76 4.051,60 6.810,22 229,55 931,41 13.726,79 36 381,30
REFIS 2026 - 48x - 50% 1.686,76 4.051,60 8.512,65 286,73 931,41 15.486,40 48 322,63
REFIS 2026 - 60x - 40% 1.686,76 4.051,60 10.215,34 344,25 931,41 17.246,61 60 287,44
REFIS 2026 - 96x - 40% CadUnico 1.686,76 4.051,60 10.215,34 344,25 931,41 17.246,61 96 179,65
"""


def test_avista_ignora_palavra_vista_do_cadastro():
    """Linha 'A vista' com \b não deve casar com 'BELA VISTA' de Bairro/Loteamento."""
    cadastro_com_bela_vista = "Bairro/Loteamento JD BELA VISTA / JARDIM BELA VISTA Q:2  L:11"
    assert not re.search(r"^.*\bA\s+vista\b.*$", cadastro_com_bela_vista, re.I | re.M)


def test_planos_extraidos_da_secao_de_planos():
    """Todos os planos, inclusive À vista, saem da seção 'Planos de Parcelamentos'."""
    expected = {
        "normal": (24286.38, 1, 24286.38),
        "avista": (6687.02, 1, 6687.02),
        "8x": (8446.78, 8, 1055.85),
        "24x": (11966.73, 24, 498.62),
        "36x": (13726.79, 36, 381.30),
        "48x": (15486.40, 48, 322.63),
        "60x": (17246.61, 60, 287.44),
    }
    for key, expected_tuple in expected.items():
        assert _plan_line(PLANS_SECTION, key) == expected_tuple, f"plano {key} divergente"


def test_planos_fora_da_secao_nao_confundem_o_parser():
    """Bairro com 'VISTA' antes da seção de planos não afeta a extração."""
    texto_completo = (
        "Bairro/Loteamento JD BELA VISTA / JARDIM BELA VISTA Q:2  L:11\n" + PLANS_SECTION
    )
    sections = texto_completo.split("Planos de Parcelamentos", 1)
    plans_text = sections[1] if len(sections) > 1 else texto_completo
    assert _plan_line(plans_text, "avista") == (6687.02, 1, 6687.02)


def test_acumulo_de_totais_entre_linhas():
    """Dois cadastros iguais dobram os totais de Normal e das parcelas de cada plano."""
    raw = {
        "Normal": 24286.38,
        "_plans": {
            "avista": {"total": 6687.02, "count": 1, "installment": 6687.02},
            "8x": {"total": 8446.78, "count": 8, "installment": 1055.85},
            "24x": {"total": 11966.73, "count": 24, "installment": 498.62},
            "36x": {"total": 13726.79, "count": 36, "installment": 381.30},
            "48x": {"total": 15486.40, "count": 48, "installment": 322.63},
            "60x": {"total": 17246.61, "count": 60, "installment": 287.44},
        },
    }
    row = apply_installment_columns(raw, 60.0)
    totals = new_plan_totals()
    accumulate_plan_totals(totals, row)
    accumulate_plan_totals(totals, row)
    assert totals["Normal"] == pytest.approx(48572.76)
    assert totals["avista"] == pytest.approx(13374.04)
    assert totals["8x"] == pytest.approx(2111.70)   # 2x a parcela de 1.055,85
    assert totals["60x"] == pytest.approx(574.88)    # 2x a parcela de 287,44


def test_celulas_mostram_valor_da_parcela():
    """Células contêm o VALOR DA PARCELA do PDF (numérico), e a soma bate."""
    raw = {
        "Normal": 3500.00,
        "_plans": {
            "avista": {"total": 1900.00, "count": 1, "installment": 1900.00},
            "8x": {"total": 2100.00, "count": 8, "installment": 262.50},
            "24x": {"total": 2400.00, "count": 24, "installment": 100.00},
            "36x": {"total": 2700.00, "count": 36, "installment": 75.00},
            "48x": {"total": 3000.00, "count": 48, "installment": 62.50},
            "60x": {"total": 3300.00, "count": 60, "installment": 55.00},
        },
    }
    row = apply_installment_columns(raw, 60.0)
    assert row["8x - 90%"] == pytest.approx(262.50)   # valor da parcela
    assert row["60x - 40%"] == pytest.approx(55.00)    # não é ocultado
    totals = new_plan_totals()
    accumulate_plan_totals(totals, row)
    assert totals["60x"] == pytest.approx(55.00)       # soma das parcelas
    assert totals["8x"] == pytest.approx(262.50)


def test_parcela_abaixo_do_minimo_gera_aviso():
    """Plano com parcela abaixo do mínimo é sinalizado, com o máximo de parcelas possível."""
    raw = {
        "Normal": 3500.00,
        "_plans": {
            "avista": {"total": 1900.00, "count": 1, "installment": 1900.00},
            "8x": {"total": 2100.00, "count": 8, "installment": 262.50},
            "24x": {"total": 2400.00, "count": 24, "installment": 100.00},
            "36x": {"total": 2700.00, "count": 36, "installment": 75.00},
            "48x": {"total": 3000.00, "count": 48, "installment": 62.50},
            "60x": {"total": 3300.00, "count": 60, "installment": 55.00},
        },
    }
    row = apply_installment_columns(raw, 60.0)
    flags = row["_flags"]
    assert "60x - 40%" in flags
    assert "55 parcelas" in flags["60x - 40%"]
    assert "R$ 60,00" in flags["60x - 40%"]
    # à vista e parcelas >= mínimo não são sinalizadas
    assert "À vista" not in flags
    assert "48x - 50%" not in flags
    assert "8x - 90%" not in flags


def test_linha_total_numerica():
    """A linha TOTAL soma Normal e planos com valores numéricos (bate com =SOMA)."""
    totals = new_plan_totals()
    totals["Normal"] = 48572.76
    totals["avista"] = 13374.04
    totals["8x"] = 16893.56
    columns = ["IdFisico", "Normal", "À vista", "8x - 90%"]
    total_row = build_total_row(totals, columns)
    assert total_row["IdFisico"] == "TOTAL"
    assert total_row["Normal"] == 48572.76
    assert total_row["À vista"] == 13374.04
    assert total_row["8x - 90%"] == 16893.56


def test_rotulo_nao_substitui_coluna_de_plano():
    """Sem colunas cadastrais visíveis, o rótulo nunca cai sobre uma coluna de plano."""
    totals = new_plan_totals()
    totals["avista"] = 13374.04
    columns = ["À vista", "8x - 90%", "Exercício"]
    total_row = build_total_row(totals, columns)
    assert total_row["Exercício"] == "TOTAL"
    assert total_row["À vista"] == 13374.04


def test_linha_total_modulo2():
    """A somatória geral do Módulo 2 soma Normal e cada plano, rotulando a linha."""
    df = pd.DataFrame([
        {"IdFisico": "1", "Local do imóvel": "RUA A", "Normal": 100.0, "À vista": 80.0, "8x - 90%": 11.25, "60x - 40%": 2.5},
        {"IdFisico": "2", "Local do imóvel": "RUA B", "Normal": 50.0, "À vista": 30.5, "8x - 90%": 4.4, "60x - 40%": 1.0},
    ])
    total_row = build_module2_total_row(df)
    assert total_row["IdFisico"] == "TOTAL"
    assert total_row["Local do imóvel"] == ""
    assert total_row["Normal"] == 150.0
    assert total_row["À vista"] == 110.5
    assert total_row["8x - 90%"] == 15.65
    assert total_row["60x - 40%"] == 3.5
