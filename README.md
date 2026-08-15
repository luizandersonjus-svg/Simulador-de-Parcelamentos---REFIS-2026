# Conversor de documentos para Excel

Aplicação Streamlit com dois módulos:

1. **Previsão de parcelamento:** lê até 100 PDFs, extrai os campos validados e gera Excel. Planos cuja parcela seja menor que R$ 60,00 — e planos posteriores — ficam vazios.
2. **Cruzamento de débitos e imóveis:** relaciona `Origem` a `Físico/IdFisico`, calcula o intervalo de exercícios, soma `Total/Subtotal` e conta parcelas normais vencidas no ano de referência.

## Executar localmente

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Publicar gratuitamente no Streamlit Community Cloud

1. Crie um repositório no GitHub e envie os arquivos desta pasta.
2. Acesse https://share.streamlit.io e conecte sua conta GitHub.
3. Clique em **Create app**, selecione o repositório e informe `app.py` como arquivo principal.
4. Publique e teste com documentos anonimizados.

Os uploads são processados em memória e não há banco de dados. A plataforma de hospedagem ainda pode produzir logs técnicos; não inclua conteúdo dos documentos em mensagens de log.
