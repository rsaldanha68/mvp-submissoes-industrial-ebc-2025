
import streamlit as st
import pandas as pd
import json, os
from datetime import datetime
from sqlalchemy import create_engine, text

st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

DATA_DIR = "data"
UPLOAD_DIR = "uploads"
PUBLIC_DIR = "public"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PUBLIC_DIR, exist_ok=True)

DB_URL = f"sqlite:///{os.path.join(DATA_DIR,'app.db')}"
engine = create_engine(DB_URL, future=True)

# --- DB bootstrap
with engine.begin() as conn:
    conn.exec_driver_sql(\"\"\"
    CREATE TABLE IF NOT EXISTS groups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        turma TEXT CHECK (turma IN ('MA6','MB6','NA6')),
        created_by TEXT,
        created_at TEXT
    );\"\"\")
    conn.exec_driver_sql(\"\"\"
    CREATE TABLE IF NOT EXISTS group_members(
        group_id INTEGER,
        student_name TEXT,
        UNIQUE(group_id, student_name)
    );\"\"\")
    conn.exec_driver_sql(\"\"\"
    CREATE TABLE IF NOT EXISTS themes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT UNIQUE,
        status TEXT CHECK (status IN ('livre','reservado')) DEFAULT 'livre',
        reserved_by TEXT,
        reserved_at TEXT,
        released_by TEXT,
        released_at TEXT
    );\"\"\")
    conn.exec_driver_sql(\"\"\"
    CREATE TABLE IF NOT EXISTS submissions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_code TEXT,
        theme_title TEXT,
        report_path TEXT,
        slides_path TEXT,
        zip_path TEXT,
        video_link TEXT,
        consent INTEGER DEFAULT 0,
        submitted_by TEXT,
        submitted_at TEXT,
        approved INTEGER DEFAULT 0
    );\"\"\")

# seed themes
if os.path.exists("themes.json"):
    with open("themes.json", "r", encoding="utf-8") as f:
        THEMES = json.load(f)
else:
    THEMES = []

with engine.begin() as conn:
    existing = pd.read_sql("SELECT title FROM themes", conn)
    have = set(existing["title"].tolist()) if not existing.empty else set()
    for t in THEMES:
        if t not in have:
            conn.execute(text("INSERT INTO themes(title,status) VALUES(:t,'livre')"), {"t": t})

def get_df(sql, **params):
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params)

def exec_sql(sql, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)

st.title("Submissões – Industrial & EBC II (2º/2025)")
tab1, tab2, tab3, tab4 = st.tabs(["1) Grupos & Temas", "2) Upload de Trabalhos", "3) Galeria/Export", "4) Administração"])

# Helpers
def list_free_themes():
    df = get_df("SELECT title FROM themes WHERE status='livre' ORDER BY title")
    return df["title"].tolist()

def list_groups():
    return get_df("SELECT id, code, turma FROM groups ORDER BY turma, code")

def group_details(code):
    dfm = get_df("SELECT student_name FROM group_members gm JOIN groups g ON gm.group_id=g.id WHERE g.code=:c", c=code)
    return dfm["student_name"].tolist() if not dfm.empty else []

def reserve_theme(theme_title, group_code, user):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM themes WHERE title=:t"), {"t": theme_title}).fetchone()
        if not row or row[0] != "livre":
            return False, "Tema já reservado."
        conn.execute(text(\"\"\"UPDATE themes
                             SET status='reservado', reserved_by=:g, reserved_at=:ts, released_by=NULL, released_at=NULL
                             WHERE title=:t\"\"\"),
                    {"g": group_code, "t": theme_title, "ts": datetime.now().isoformat(timespec="seconds")})
    return True, "Reservado com sucesso."

def release_theme(theme_title, user):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM themes WHERE title=:t"), {"t": theme_title}).fetchone()
        if not row or row[0] != "reservado":
            return False, "Tema não está reservado."
        conn.execute(text(\"\"\"UPDATE themes
                             SET status='livre', reserved_by=NULL, reserved_at=NULL,
                                 released_by=:u, released_at=:ts
                             WHERE title=:t\"\"\"),
                    {"u": user, "t": theme_title, "ts": datetime.now().isoformat(timespec="seconds")})
    return True, "Tema liberado."

with tab1:
    st.subheader("Formação de grupos (5 a 6 alunos)")
    colA, colB = st.columns(2)
    with colA:
        turma = st.selectbox("Turma", ["MA6","MB6","NA6"])
        code = st.text_input("Código do grupo (ex.: MA6G1)")
    with colB:
        created_by = st.text_input("Seu nome (quem está registrando)")
        if st.button("Criar grupo"):
            if not code or not turma:
                st.error("Informe turma e código do grupo.")
            elif not code.upper().startswith(turma):
                st.error("O código do grupo deve começar com a turma (ex.: MA6G1).")
            else:
                try:
                    exec_sql("INSERT INTO groups(code,turma,created_by,created_at) VALUES(:c,:t,:u,:ts)",
                             c=code.strip().upper(), t=turma, u=created_by.strip(), ts=datetime.now().isoformat(timespec="seconds"))
                    st.success("Grupo criado.")
                except Exception as e:
                    st.error(f"Não foi possível criar (código já existe?): {e}")

    st.markdown("---")
    st.subheader("Adicionar membros (5–6)")
    gdf = list_groups()
    if gdf.empty:
        st.info("Crie um grupo primeiro.")
    else:
        sel_group = st.selectbox("Selecione o grupo", gdf["code"].tolist())
        members = group_details(sel_group)
        new_member = st.text_input("Adicionar membro (nome completo)")
        colx, coly = st.columns(2)
        if colx.button("Adicionar ao grupo"):
            if not new_member.strip():
                st.error("Informe o nome do membro.")
            else:
                gid = gdf[gdf["code"] == sel_group]["id"].iloc[0]
                try:
                    exec_sql("INSERT INTO group_members(group_id,student_name) VALUES(:g,:n)", g=gid, n=new_member.strip())
                    st.success("Membro adicionado.")
                except Exception as e:
                    st.error(f"Erro ao adicionar (duplicado?): {e}")
        if coly.button("Remover último membro"):
            gid = gdf[gdf["code"] == sel_group]["id"].iloc[0]
            exec_sql("DELETE FROM group_members WHERE rowid IN (SELECT rowid FROM group_members WHERE group_id=:g ORDER BY rowid DESC LIMIT 1)", g=gid)
            st.warning("Último membro removido.")
        st.write("Membros atuais:", members, f"({len(members)}/6)")

    st.markdown("---")
    st.subheader("Reserva de tema (exclusiva)")
    if gdf.empty:
        st.info("Crie um grupo e adicione 5–6 membros antes de reservar tema.")
    else:
        sel_group2 = st.selectbox("Grupo para reservar", gdf["code"].tolist(), key="reserve_group")
        members2 = group_details(sel_group2)
        if len(members2) < 5 or len(members2) > 6:
            st.error("Este grupo precisa ter entre 5 e 6 membros para reservar tema.")
        else:
            free_list = list_free_themes()
            theme_choice = st.selectbox("Temas disponíveis", free_list)
            booked_by = st.text_input("Seu nome (quem está reservando)")
            cols = st.columns(2)
            if cols[0].button("Reservar tema"):
                ok, msg = reserve_theme(theme_choice, sel_group2, booked_by.strip())
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
            my_reserved = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=sel_group2)["title"].tolist()
            release_sel = st.selectbox("Liberar tema reservado (do seu grupo)", my_reserved) if my_reserved else None
            released_by = st.text_input("Seu nome (quem está liberando)")
            if cols[1].button("Liberar tema"):
                if not release_sel:
                    st.error("Seu grupo não possui tema reservado.")
                else:
                    ok, msg = release_theme(release_sel, released_by.strip())
                    if ok:
                        st.warning(msg)
                    else:
                        st.error(msg)

    st.markdown("---")
    st.subheader("Status dos temas")
    tdf = get_df("SELECT title, status, reserved_by, reserved_at, released_by, released_at FROM themes ORDER BY status DESC, title")
    st.dataframe(tdf, use_container_width=True)

with tab2:
    st.subheader("Upload de trabalhos finais")
    gdf = list_groups()
    if gdf.empty:
        st.info("Crie um grupo primeiro.")
    else:
        group = st.selectbox("Grupo", gdf["code"].tolist())
        tdf = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=group)
        theme = tdf["title"].iloc[0] if not tdf.empty else None
        if not theme:
            st.error("Este grupo ainda não reservou um tema.")
        else:
            st.write("Tema do grupo:", f"**{theme}**")
            report = st.file_uploader("Relatório (PDF)", type=["pdf"])
            slides = st.file_uploader("Apresentação (PPTX ou PDF)", type=["pptx","pdf"])
            bundle = st.file_uploader("Materiais adicionais (ZIP)", type=["zip"])
            video = st.text_input("Link do vídeo (YouTube, Stream, etc.)")
            consent = st.checkbox("Cedo os direitos patrimoniais à PUC-SP para divulgação acadêmica/extensionista, com crédito aos autores.")
            submitted_by = st.text_input("Seu nome (quem está submetendo)")
            if st.button("Enviar"):
                if not consent:
                    st.error("É necessário marcar a cessão de direitos para enviar.")
                else:
                    gdir = os.path.join(UPLOAD_DIR, group.replace('/','_'))
                    os.makedirs(gdir, exist_ok=True)
                    def save_file(up, name):
                        if up is None: return None
                        p = os.path.join(gdir, name)
                        with open(p, "wb") as f:
                            f.write(up.getbuffer())
                        return p
                    rpath = save_file(report, "relatorio.pdf")
                    spath = save_file(slides, "apresentacao."+ (slides.name.split('.')[-1] if slides else "pdf"))
                    zpath = save_file(bundle, "materiais.zip")
                    exec_sql(\"\"\"INSERT INTO submissions(group_code, theme_title, report_path, slides_path, zip_path, video_link,
                                consent, submitted_by, submitted_at, approved)
                                VALUES(:g,:t,:r,:s,:z,:v,:c,:u,:ts,0)\"\"\" ,
                             g=group, t=theme, r=rpath, s=spath, z=zpath, v=video, c=1 if consent else 0,
                             u=submitted_by.strip(), ts=datetime.now().isoformat(timespec="seconds"))
                    st.success("Submissão recebida.")

    st.markdown("---")
    st.subheader("Submissões do seu grupo")
    if gdf.empty:
        pass
    else:
        group2 = st.selectbox("Ver submissões do grupo", gdf["code"].tolist(), key="sub_view")
        sdf = get_df("SELECT id, theme_title, report_path, slides_path, zip_path, video_link, submitted_by, submitted_at, approved FROM submissions WHERE group_code=:g ORDER BY submitted_at DESC", g=group2)
        st.dataframe(sdf, use_container_width=True)

with tab3:
    st.subheader("Exportar aprovados para galeria")
    pwd = st.text_input("Senha de professor(a) para exportar", type="password")
    if pwd == st.secrets.get("ADMIN_PWD", "admin"):
        approved = get_df("SELECT group_code, theme_title, submitted_at, video_link FROM submissions WHERE approved=1 ORDER BY submitted_at DESC")
        st.write("Aprovados atuais:", len(approved))
        if st.button("Gerar JSON e site estático"):
            out = []
            for _, row in approved.iterrows():
                members = group_details(row["group_code"])
                out.append({
                    "group": row["group_code"],
                    "theme": row["theme_title"],
                    "members": members,
                    "submitted_at": row["submitted_at"],
                    "video_link": row["video_link"]
                })
            with open(os.path.join(PUBLIC_DIR, "submissions.json"), "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            os.system("python gallery_builder.py")
            st.success("Gerado em ./public (index.html + submissions.json)")
            st.download_button("Baixar submissions.json", data=open(os.path.join(PUBLIC_DIR,"submissions.json"),"rb").read(), file_name="submissions.json", mime="application/json")
            st.download_button("Baixar index.html", data=open(os.path.join(PUBLIC_DIR,"index.html"),"rb").read(), file_name="index.html", mime="text/html")
    else:
        st.info("Digite a senha para exportar (padrão: admin).")

with tab4:
    st.subheader("Administração")
    admin_pwd = st.text_input("Senha de professor(a)", type="password", key="adm")
    if admin_pwd == st.secrets.get("ADMIN_PWD", "admin"):
        st.write("Aprovar submissões para vitrine pública")
        sdf = get_df("SELECT id, group_code, theme_title, submitted_at, approved FROM submissions ORDER BY submitted_at DESC")
        st.dataframe(sdf, use_container_width=True)
        ids = st.multiselect("IDs para aprovar", sdf["id"].tolist())
        if st.button("Aprovar selecionadas"):
            for i in ids:
                exec_sql("UPDATE submissions SET approved=1 WHERE id=:i", i=int(i))
            st.success("Aprovadas.")
        st.markdown("---")
        st.write("Temas (liberar/travar manualmente, se necessário)")
        tdf = get_df("SELECT title, status, reserved_by, reserved_at, released_by, released_at FROM themes ORDER BY title")
        st.dataframe(tdf, use_container_width=True)
    else:
        st.info("Digite a senha (padrão: admin).")

st.caption("MVP – Submissões Industrial & EBC II (2º/2025).")
