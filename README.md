
# MVP – Submissões (Industrial & EBC II) – 2º/2025

## Conteúdo
- `app.py`: aplicativo **Streamlit** completo (grupos, temas, uploads, aprovação, export).
- `themes.json`: lista dos **50 temas**.
- `gallery_builder.py` + `templates/index.html.j2`: gerador da **galeria estática**.
- `requirements.txt`: dependências.
- Pastas: `data/` (SQLite), `uploads/` (arquivos), `public/` (saída do site).

## Como rodar local
```bash
pip install -r requirements.txt
streamlit run app.py
```
Abra o link local exibido pelo Streamlit.

**Senha de admin (padrão)**: `admin` — troque em `st.secrets` após o deploy.

## Fluxo
1. **Grupos & Temas**: crie o grupo (ex.: MA6G1), adicione **5–6 alunos** e **reserve** 1 tema (exclusivo).
2. **Upload**: PDF + slides + ZIP e link de vídeo; aceite a **cessão de direitos à PUC‑SP**.
3. **Admin**: aprove para vitrine pública.
4. **Galeria**: gere `public/index.html` + `public/submissions.json` e publique (GitHub Pages / Azure Static Web Apps).
