import os, sys, json, pathlib, re
import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
sys.path.insert(0, str(pathlib.Path(__file__).parent))  # Ajuste para módulos internos


APP_ROOT = pathlib.Path(__file__).parent.resolve()
PKG_ROOTS = [APP_ROOT, APP_ROOT / "app", APP_ROOT / "app" / "modules"]
for p in map(str, PKG_ROOTS):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.modules.import_txt import parse_puc_txt, upsert_students_and_enroll

st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

# Pastas
DATA_DIR = "data"; UPLOAD_DIR = "uploads"; PUBLIC_DIR = "public"
for p in (DATA_DIR, UPLOAD_DIR, PUBLIC_DIR):
    os.makedirs(p, exist_ok=True)

# Banco (SQLite)
DB_URL = f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}"
engine = create_engine(DB_URL, future=True)

# --- Tabelas essenciais ---
with engine.begin() as conn:
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS groups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        turma TEXT,
        course_code TEXT DEFAULT 'JOINT', -- IND | EBCII | JOINT
        created_by TEXT,
        created_at TEXT
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS group_members(
        group_id INTEGER,
        student_name TEXT,
        UNIQUE(group_id, student_name)
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS themes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number INTEGER,
        title TEXT UNIQUE,
        category TEXT,
        status TEXT CHECK (status IN ('livre','reservado')) DEFAULT 'livre',
        reserved_by TEXT,
        reserved_at TEXT,
        released_by TEXT,
        released_at TEXT
    );""")
    conn.exec_driver_sql("""
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
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS students(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ra TEXT UNIQUE,
        name TEXT NOT NULL,
        email TEXT,
        turma TEXT,
        active INTEGER DEFAULT 1
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS professors(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        role TEXT CHECK (role IN ('admin','docente')) DEFAULT 'docente',
        pin TEXT
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL,
        instructor_id INTEGER NOT NULL,
        score REAL,
        liked INTEGER DEFAULT 0,
        created_at TEXT,
        UNIQUE(submission_id, instructor_id)
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS pending_students(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        turma TEXT NOT NULL,
        requester TEXT
    );""")

# seeds iniciais
with engine.begin() as conn:
    conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('IND','Economia Industrial')"))
    conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('EBCII','Economia Brasileira II')"))
    conn.execute(text("INSERT OR IGNORE INTO semesters(term) VALUES('2025/2')"))

def get_df(sql: str, **params):
    """Retorna um DataFrame para o resultado da consulta SQL."""
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params)

def exec_sql(sql: str, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)

def list_free_themes(category: str | None = None):
    if category and category != "Todos":
        df = get_df("SELECT title FROM themes WHERE status='livre' AND category=:c ORDER BY number", c=category)
    else:
        df = get_df("SELECT title FROM themes WHERE status='livre' ORDER BY number")
    return df["title"].tolist() if not df.empty else []

def list_groups():
    return get_df("SELECT id, code, turma, course_code FROM groups ORDER BY turma, code")

def list_groups_user():
    """Lista grupos conforme o usuário logado (docente vê todos, aluno vê apenas os seus)."""
    if st.session_state.get("user_type") == "teacher":
        return list_groups()
    else:
        return get_df("""SELECT g.id, g.code, g.turma, g.course_code
                         FROM groups g JOIN group_members gm ON gm.group_id=g.id
                         WHERE gm.student_name=:name
                         ORDER BY g.turma, g.code""", name=st.session_state.get("name"))

def group_details(code: str):
    dfm = get_df("""SELECT gm.student_name FROM group_members gm
                    JOIN groups g ON gm.group_id=g.id WHERE g.code=:c""", c=code)
    return dfm["student_name"].tolist() if not dfm.empty else []

def reserve_theme(theme_title: str, group_code: str):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM themes WHERE title=:t"), {"t": theme_title}).fetchone()
        if not row or row[0] != "livre":
            return False, "Tema já reservado."
        conn.execute(text("""UPDATE themes SET status='reservado', reserved_by=:g, reserved_at=:ts,
                           released_by=NULL, released_at=NULL WHERE title=:t"""),
                     {"g": group_code, "t": theme_title,
                      "ts": datetime.now().isoformat(timespec="seconds")})
    return True, "Reservado com sucesso."

def release_theme(theme_title: str, user: str):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM themes WHERE title=:t"), {"t": theme_title}).fetchone()
        if not row or row[0] != "reservado":
            return False, "Tema não está reservado."
        conn.execute(text("""UPDATE themes SET status='livre', reserved_by=NULL, reserved_at=NULL,
                           released_by=:u, released_at=:ts WHERE title=:t"""),
                     {"u": user, "t": theme_title,
                      "ts": datetime.now().isoformat(timespec="seconds")})
    return True, "Tema liberado."

def students_unassigned():
    return get_df("""SELECT s.id, s.ra, s.name, s.turma FROM students s
                      LEFT JOIN (SELECT DISTINCT gm.student_name AS name FROM group_members gm) x
                        ON x.name = s.name
                    WHERE x.name IS NULL
                    ORDER BY s.turma, s.name""")

def link_student_to_group(student_id: int, group_code: str):
    gdf = list_groups()
    if gdf.empty:
        raise RuntimeError("Nenhum grupo cadastrado.")
    row = gdf[gdf["code"] == group_code]
    if row.empty:
        raise RuntimeError("Grupo não encontrado.")
    gid = int(row["id"].iloc[0])
    srow = get_df("SELECT name FROM students WHERE id=:i", i=int(student_id))
    if srow.empty:
        raise RuntimeError("Aluno não encontrado.")
    name = srow["name"].iloc[0]
    exec_sql("INSERT OR IGNORE INTO group_members(group_id,student_name) VALUES(:g,:n)", g=gid, n=name)

# --- UI ---
if "user_type" not in st.session_state:
    st.title("Submissões – Industrial & EBC II (2º/2025)")
    st.subheader("Login")
    user_type = st.radio("Sou:", ["Aluno", "Docente"])
    if user_type == "Aluno":
        ra_input = st.text_input("RA", key="login_ra")
        email_input = st.text_input("E-mail institucional", key="login_student_email")
    else:
        email_input = st.text_input("E-mail institucional", key="login_teacher_email")
        pin_input = st.text_input("PIN", type="password", key="login_pin")
    if st.button("Entrar"):
        if user_type == "Aluno":
            df = get_df("SELECT name, turma, email, ra FROM students WHERE ra=:ra AND email=:email", ra=ra_input.strip(), email=email_input.strip())
            if not df.empty:
                student = df.iloc[0]
                st.session_state["user_type"] = "student"
                st.session_state["name"] = student["name"]
                st.session_state["turma"] = student["turma"]
                st.session_state["email"] = student["email"]
                st.session_state["ra"] = student["ra"]
                st.experimental_rerun()
            else:
                st.error("Aluno não encontrado. Verifique o RA e e-mail.")
        else:
            dfp = get_df("SELECT id, name, email, role FROM professors WHERE email=:email AND pin=:pin", email=email_input.strip(), pin=pin_input.strip())
            if not dfp.empty:
                prof = dfp.iloc[0]
                st.session_state["user_type"] = "teacher"
                st.session_state["name"] = prof["name"]
                st.session_state["turma"] = None
                st.session_state["email"] = prof["email"]
                st.session_state["id"] = int(prof["id"])
                st.experimental_rerun()
            else:
                st.error("Credenciais inválidas. Tente novamente.")
    st.stop()

st.title("Submissões – Industrial & EBC II (2º/2025)")
if st.session_state.get("user_type") == "teacher":
    tab_names = ["1) Grupos & Temas", "2) Upload", "3) Galeria/Avaliação", "4) Administração", "5) Alunos & Docentes"]
else:
    tab_names = ["1) Grupos & Temas", "2) Upload"]
tabs = st.tabs(tab_names)

# 1) Grupos & Temas
with tabs[0]:
    st.subheader("Criar grupo (5–6 alunos)")
    # Criar novo grupo
    if st.session_state["user_type"] == "teacher":
        c1, c2 = st.columns(2)
        with c1:
            turma_select = st.selectbox("Turma", ["MA6","MB6","NA6","NB6"])
        with c2:
            creator_name = st.text_input("Criador (nome)", value=st.session_state.get("name", ""))
            disc_select = st.selectbox("Disciplina do grupo", ["JOINT","IND","EBCII"])
    else:
        # Exibe a turma do aluno logado
        st.write(f"Turma: **{st.session_state.get('turma', '')}**")
    if st.button("Criar grupo"):
        try:
            if st.session_state.get("user_type") == "teacher":
                base_turma = turma_select
                created_by_val = creator_name.strip() if creator_name.strip() else st.session_state.get("name")
                course_val = disc_select
            else:
                base_turma = st.session_state.get("turma")
                created_by_val = st.session_state.get("name")
                course_val = "JOINT"
            df_count = get_df("SELECT COUNT(*) AS cnt FROM groups WHERE turma=:t", t=base_turma)
            next_num = int(df_count["cnt"].iloc[0]) + 1
            group_code = f"G{next_num}{base_turma}"
            exec_sql("INSERT INTO groups(code,turma,course_code,created_by,created_at) VALUES(:c,:t,:cc,:u,:ts)", c=group_code, t=base_turma, cc=course_val, u=created_by_val, ts=datetime.now().isoformat(timespec="seconds"))
            st.success(f"Grupo {group_code} criado.")
        except Exception as e:
            st.error(f"Erro ao criar: {e}")
    st.markdown("---")
    st.subheader("Adicionar membros (5–6)")
    gdf = list_groups_user()
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
                gid = int(gdf[gdf["code"] == sel_group]["id"].iloc[0])
                try:
                    exec_sql("INSERT INTO group_members(group_id,student_name) VALUES(:g,:n)", g=gid, n=new_member.strip())
                    st.success("Membro adicionado.")
                except Exception as e:
                    st.error(f"Erro ao adicionar: {e}")
        if coly.button("Remover último membro"):
            gid = int(gdf[gdf["code"] == sel_group]["id"].iloc[0])
            exec_sql("DELETE FROM group_members WHERE rowid IN (SELECT rowid FROM group_members WHERE group_id=:g ORDER BY rowid DESC LIMIT 1)", g=gid)
            st.warning("Último membro removido.")
        st.write("Membros atuais:", members, f"({len(members)}/6)")

    st.markdown("---")
    st.subheader("Reserva de tema (exclusiva)")
    if gdf.empty:
        st.info("Crie um grupo e adicione membros antes de reservar um tema.")
    else:
        sel_group2 = st.selectbox("Grupo para reservar", gdf["code"].tolist(), key="reserve_group")
        members2 = group_details(sel_group2)
        # Bloqueia reserva de tema de acordo com data e tamanho do grupo
        today = datetime.today().date()
        cutoff_date = datetime(2025, 3, 30).date()
        required_min = 5 if today <= cutoff_date else 3
        if len(members2) < required_min or len(members2) > 6:
            st.error(f"Este grupo precisa ter entre {required_min} e 6 membros para reservar um tema.")
        else:
            cat_res = st.selectbox("Filtrar por categoria", ["Todos","Privatização","Concessão","PPP","Financiamento/BNDES","Outro"], key="cat_res")
            free_list = list_free_themes(cat_res)
            theme_choice = st.selectbox("Temas disponíveis", free_list)
            cols = st.columns(2)
            if cols[0].button("Reservar tema"):
                ok, msg = reserve_theme(theme_choice, sel_group2)
                st.success(msg) if ok else st.error(msg)
            my_reserved = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=sel_group2)["title"].tolist()
            release_sel = st.selectbox("Liberar tema reservado (do seu grupo)", my_reserved) if my_reserved else None
            released_by = st.text_input("Seu nome (quem está liberando)")
            if cols[1].button("Liberar tema"):
                if not release_sel:
                    st.error("Seu grupo não possui tema reservado.")
                else:
                    ok, msg = release_theme(release_sel, released_by.strip())
                    st.warning(msg) if ok else st.error(msg)
    st.markdown("---")
    st.subheader("Status dos temas")
    cat_filter = st.selectbox("Categoria", ["Todos","Privatização","Concessão","PPP","Financiamento/BNDES","Outro"], key="cat_view")
    if cat_filter == "Todos":
        tdf = get_df("SELECT number, title, category, status, reserved_by, reserved_at FROM themes ORDER BY status DESC, number")
    else:
        tdf = get_df("SELECT number, title, category, status, reserved_by, reserved_at FROM themes WHERE category=:c ORDER BY status DESC, number", c=cat_filter)
    st.dataframe(tdf, use_container_width=True)

# 2) Upload
with tabs[1]:
    st.subheader("Upload de trabalhos finais")
    gdf = list_groups_user()
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
                    gdir = os.path.join(UPLOAD_DIR, group.replace('/', '_'))
                    os.makedirs(gdir, exist_ok=True)
                    def save_file(up, name):
                        if up is None:
                            return None
                        p = os.path.join(gdir, name)
                        with open(p, "wb") as f:
                            f.write(up.getbuffer())
                        return p
                    rpath = save_file(report, "relatorio.pdf")
                    spath = save_file(slides, "apresentacao." + (slides.name.split('.')[-1] if slides else "pdf"))
                    zpath = save_file(bundle, "materiais.zip")
                    exec_sql("""INSERT INTO submissions(group_code, theme_title, report_path, slides_path, zip_path, video_link,
                                consent, submitted_by, submitted_at, approved)
                                VALUES(:g,:t,:r,:s,:z,:v,:c,:u,:ts)""",
                             g=group, t=theme, r=rpath, s=spath, z=zpath, v=video, c=1 if consent else 0,
                             u=submitted_by.strip(), ts=datetime.now().isoformat(timespec="seconds"))
                    st.success("Submissão recebida.")
    st.markdown("---")
    st.subheader("Submissões do seu grupo")
    if not gdf.empty:
        group2 = st.selectbox("Ver submissões do grupo", gdf["code"].tolist(), key="sub_view")
        sdf = get_df("""SELECT id, theme_title, report_path, slides_path, zip_path, video_link,
                       submitted_by, submitted_at, approved FROM submissions
                       WHERE group_code=:g ORDER BY submitted_at DESC""", g=group2)
        st.dataframe(sdf, use_container_width=True)

# 3) Galeria/Avaliação
if st.session_state.get("user_type") == "teacher":
    with tabs[2]:
        st.subheader("Avaliação por docentes")
        email = st.text_input("E-mail institucional")
        pin = st.text_input("PIN", type="password")
        ok = False
        if st.button("Entrar"):
            dfp = get_df("SELECT * FROM professors WHERE email=:e AND pin=:p", e=email, p=pin)
            ok = not dfp.empty
            st.success("Acesso ok.") if ok else st.error("Credenciais inválidas.")
        if ok:
            pr = get_df("SELECT id,name FROM professors WHERE email=:e", e=email)
            instr_id = pr["id"].iloc[0]
            sdf = get_df("SELECT id, group_code, theme_title, submitted_at FROM submissions WHERE approved=1 ORDER BY submitted_at DESC")
            st.dataframe(sdf, use_container_width=True)
            sid = st.selectbox("Trabalho (ID)", sdf["id"].tolist() if not sdf.empty else [])
            like = st.toggle("Curtir")
            score = st.slider("Nota", 0, 10, 8)
            if st.button("Salvar avaliação"):
                exec_sql("""INSERT INTO reviews(submission_id,instructor_id,liked,score,created_at)
                            VALUES(:i,:p,:l,:s,:ts)
                            ON CONFLICT(submission_id, instructor_id) DO UPDATE
                            SET liked=:l, score=:s, created_at=:ts""",
                         i=int(sid), p=int(instr_id), l=int(like), s=float(score),
                         ts=datetime.now().isoformat(timespec="seconds"))
                st.success("Ok.")
        st.markdown("---")
        st.subheader("Métricas (aprovados)")
        m = get_df("""SELECT s.id, s.group_code, s.theme_title,
                      ROUND(AVG(rv.score),2) AS media, SUM(rv.liked) AS likes
                      FROM submissions s LEFT JOIN reviews rv ON rv.submission_id=s.id
                      WHERE s.approved=1
                      GROUP BY s.id ORDER BY likes DESC, media DESC""")
        st.dataframe(m, use_container_width=True)

    with tabs[3]:
        st.subheader("Aprovar submissões para galeria")
        sdf = get_df("SELECT id, group_code, theme_title, submitted_at, approved FROM submissions ORDER BY submitted_at DESC")
        st.dataframe(sdf, use_container_width=True)
        ids = st.multiselect("IDs para aprovar", sdf["id"].tolist())
        if st.button("Aprovar selecionadas"):
            for i in ids:
                exec_sql("UPDATE submissions SET approved=1 WHERE id=:i", i=int(i))
            st.success("Aprovadas.")
        st.markdown("---")
        st.subheader("Temas (liberar/travar, importar/atualizar)")
        tdf = get_df("SELECT number, title, category, status, reserved_by, reserved_at FROM themes ORDER BY number")
        st.dataframe(tdf, use_container_width=True)
        up_themes = st.file_uploader("Importar JSON de temas", type=["json"], key="themes_up")
        if up_themes and st.button("Carregar temas"):
            tmp = os.path.join(DATA_DIR, "_themes_upload.json")
            with open(tmp, "wb") as f:
                f.write(up_themes.read())
            addn = ensure_themes_from_json(tmp)
            st.success(f"Temas adicionados: {addn}. (itens existentes pelo título são ignorados)")
        st.markdown("---")
        st.subheader("Aprovar alunos pendentes")
        pend_df = get_df("SELECT id, name, turma, requester FROM pending_students")
        if pend_df.empty:
            st.info("Não há alunos pendentes no momento.")
        else:
            for _index, _row in pend_df.iterrows():
                col1, col2, col3, col4 = st.columns([3, 2, 3, 2])
                col1.text(_row["name"])
                col2.text(_row["turma"])
                col3.text(_row["requester"])
                approve_key = f"approve_{_row['id']}"
                if col4.button("Aprovar", key=approve_key):
                    with engine.begin() as conn:
                        conn.execute(text("INSERT INTO students(ra, name, email, turma) VALUES(:ra, :name, :email, :turma)"),
                                     {"ra": None, "name": _row["name"], "email": None, "turma": _row["turma"]})
                        conn.execute(text("DELETE FROM pending_students WHERE id=:id"), {"id": int(_row["id"])})
                    st.success(f"Aluno {_row['name']} aprovado e adicionado.")
                    st.experimental_rerun()

    with tabs[4]:
        st.subheader("Importar alunos (CSV) — colunas: ra,name,email,turma")
        up_alunos = st.file_uploader("CSV de alunos", type=["csv"])
        if up_alunos and st.button("Processar CSV"):
            df = pd.read_csv(up_alunos)
            with engine.begin() as conn:
                for row in df.to_dict(orient="records"):
                    conn.execute(text("""INSERT OR IGNORE INTO students(ra,name,email,turma)
                                       VALUES(:ra,:name,:email,:turma)"""), row)
            st.success(f"{len(df)} aluno(s) processados.")
        st.markdown("---")
        st.subheader("Importar listas PUC (TXT) — múltiplos arquivos")
        term = st.text_input("Semestre (term)", value="2025/2")
        up_txt = st.file_uploader("Arquivos .txt", type=["txt"], accept_multiple_files=True, key="txts")
        if up_txt and st.button("Processar TXT"):
            from modules.import_txt import parse_puc_txt, upsert_students_and_enroll
            ok_count = 0
            temp_dir = pathlib.Path(DATA_DIR) / "_tmp"; temp_dir.mkdir(parents=True, exist_ok=True)
            disc_map = {"ECONOMIA INDUSTRIAL":"IND", "EBC II":"EBCII"}
            for upl in up_txt:
                fp = temp_dir / upl.name
                fp.write_bytes(upl.read())
                meta = parse_puc_txt(str(fp))
                turma_txt = (meta.get("turma") or "")
                disc_code = disc_map.get(meta.get("disciplina"), "IND")
                if not turma_txt:
                    st.warning(f"{upl.name}: turma não detectada; ignorado.")
                    continue
                upsert_students_and_enroll(engine, term, disc_code, turma_txt, meta.get("students", []))
                ok_count += 1
            st.success(f"TXT processados: {ok_count}")
        st.markdown("---")
        st.subheader("Alunos sem grupo → alocar em grupos")
        sdf = students_unassigned()
        st.dataframe(sdf, use_container_width=True)
        col_a, col_b = st.columns(2)
        sid = col_a.text_input("ID do aluno (coluna 'id') para alocar")
        gcode = col_b.text_input("Grupo destino (ex.: MA6G1)")
        if st.button("Alocar aluno"):
            try:
                link_student_to_group(int(sid), gcode.strip().upper())
                st.success("Aluno alocado.")
            except Exception as e:
                st.error(str(e))
        st.markdown("---")
        st.subheader("Docentes (PIN)")
        name = st.text_input("Nome")
        email = st.text_input("E-mail")
        role = st.selectbox("Papel", ["docente","admin"])
        pinp = st.text_input("PIN", type="password", key="add_pin")
        if st.button("Salvar docente"):
            exec_sql("""INSERT INTO professors(name,email,role,pin) VALUES(:n,:e,:r,:p)
                        ON CONFLICT(email) DO UPDATE SET name=:n, role=:r, pin=:p""", n=name, e=email, r=role, p=pinp)
            st.success("Docente salvo/atualizado.")

st.caption("MVP – Submissões Industrial & EBC II (2º/2025)")
