# app.py
import os, sys, json, pathlib, re
import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime, timezone
from dateutil import parser as dtparser

# --- permitir importar "app/modules" no Streamlit Cloud ---
APP_ROOT = pathlib.Path(__file__).parent.resolve()
for p in (APP_ROOT, APP_ROOT / "app", APP_ROOT / "app" / "modules"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from modules.import_txt import parse_puc_txt, upsert_students_and_enroll

st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

# Pastas
DATA_DIR = "data"; UPLOAD_DIR = "uploads"; PUBLIC_DIR = "public"
for p in (DATA_DIR, UPLOAD_DIR, PUBLIC_DIR):
    os.makedirs(p, exist_ok=True)

# Banco (SQLite)
DB_URL = f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}"
engine = create_engine(DB_URL, future=True)

# -------------------------------
# MIGRAÇÃO/SCHEMA + SEEDS
# -------------------------------
SCHEMA_VERSION = 3

def ensure_schema_and_migrate(engine):
    """Cria todas as tabelas necessárias antes de rodar seeds (idempotente)."""
    with engine.begin() as conn:
        cur_ver = conn.exec_driver_sql("PRAGMA user_version").scalar() or 0

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
        CREATE TABLE IF NOT EXISTS disciplines(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT NOT NULL
        );""")
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS semesters(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT UNIQUE NOT NULL
        );""")
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS offerings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discipline_id INTEGER NOT NULL,
            term TEXT NOT NULL,
            turma TEXT NOT NULL,
            instructor_id INTEGER,
            UNIQUE(discipline_id, term, turma)
        );""")
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS enrollments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            offering_id INTEGER NOT NULL,
            active INTEGER DEFAULT 1,
            UNIQUE(student_id, offering_id)
        );""")
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS pending_students(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            turma TEXT NOT NULL,
            requester TEXT,
            requested_at TEXT
        );""")

        if cur_ver < SCHEMA_VERSION:
            conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION};")

def seed_minimo(engine):
    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('IND','Economia Industrial')"))
        conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('EBCII','Economia Brasileira II')"))
        conn.execute(text("INSERT OR IGNORE INTO semesters(term) VALUES('2025/2')"))

ensure_schema_and_migrate(engine)
seed_minimo(engine)

# -------------------------------
# UTILS DB
# -------------------------------
def get_df(sql: str, **params):
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params)

def exec_sql(sql: str, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)

def ensure_themes_from_json(path_json: str) -> int:
    """Importa novos temas de um JSON (ignora títulos já existentes)."""
    if not os.path.exists(path_json):
        return 0
    with open(path_json, "r", encoding="utf-8") as f:
        items = json.load(f) or []
    inserted = 0
    with engine.begin() as conn:
        existing = pd.read_sql("SELECT title FROM themes", conn)
        have = set(existing["title"].tolist()) if not existing.empty else set()
        for it in items:
            # aceita array de strings ou objetos com {number,title,category}
            if isinstance(it, str):
                title = it.strip()
                number = None
                category = "Outro"
            else:
                title = (it.get("title") or "").strip()
                number = it.get("number")
                category = it.get("category") or "Outro"
            if not title or title in have:
                continue
            conn.execute(text("""INSERT INTO themes(number,title,category,status)
                                 VALUES(:n,:t,:c,'livre')"""),
                         {"n": int(number) if number not in (None, "") else None,
                          "t": title, "c": category})
            inserted += 1
    return inserted

# -------------------------------
# LÓGICA DE TEMAS/GRUPOS
# -------------------------------
def list_free_themes(category: str | None = None):
    if category and category != "Todos":
        df = get_df("SELECT title FROM themes WHERE status='livre' AND category=:c ORDER BY number", c=category)
    else:
        df = get_df("SELECT title FROM themes WHERE status='livre' ORDER BY number")
    return df["title"].tolist() if not df.empty else []

def list_groups():
    return get_df("SELECT id, code, turma, course_code FROM groups ORDER BY turma, code")

def list_groups_user():
    """Docente vê todos; aluno vê apenas os seus grupos."""
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

# -------------------------------
# REGRAS DE RESERVA (DATA & MÍNIMO)
# -------------------------------
def now_utc():
    return datetime.now(timezone.utc)

def can_reserve_theme_count(member_count: int) -> tuple[bool, str]:
    deadline = dtparser.parse(st.secrets.get("RESERVE_DEADLINE", "2025-03-30T23:59:00-03:00"))
    before = int(st.secrets.get("MIN_MEMBERS_BEFORE_DEADLINE", 5))
    after  = int(st.secrets.get("MIN_MEMBERS_AFTER_DEADLINE", 3))
    min_req = before if now_utc() <= deadline else after
    if member_count < min_req:
        return False, f"Para reservar tema, seu grupo precisa de pelo menos {min_req} membro(s) neste período."
    if member_count > 6:
        return False, "Grupo não pode ter mais de 6 membros."
    return True, ""

# -------------------------------
# LOGIN
# -------------------------------
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
            df = get_df("""SELECT name, turma, email, ra FROM students
                           WHERE ra=:ra AND email=:email""",
                        ra=ra_input.strip(), email=email_input.strip())
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
            dfp = get_df("""SELECT id, name, email, role FROM professors
                            WHERE email=:email AND pin=:pin""",
                         email=email_input.strip(), pin=pin_input.strip())
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

# -------------------------------
# (1) GRUPOS & TEMAS
# -------------------------------
with tabs[0]:
    st.subheader("Criar grupo (5–6 alunos)")
    # Docente pode escolher turma; aluno usa a própria
    if st.session_state["user_type"] == "teacher":
        c1, c2 = st.columns(2)
        with c1:
            turma_select = st.selectbox("Turma", ["MA6","MB6","NA6","NB6"])
        with c2:
            creator_name = st.text_input("Criador (nome)", value=st.session_state.get("name", ""), key="creator_name")
            disc_select = st.selectbox("Disciplina do grupo", ["JOINT","IND","EBCII"])
    else:
        st.write(f"Turma: **{st.session_state.get('turma', '')}**")
    if st.button("Criar grupo", key="btn_create_group"):
        try:
            if st.session_state.get("user_type") == "teacher":
                base_turma = turma_select
                created_by_val = creator_name.strip() if creator_name.strip() else st.session_state.get("name")
                course_val = disc_select
            else:
                base_turma = st.session_state.get("turma")
                created_by_val = st.session_state.get("name")
                course_val = "JOINT"
            # sequencial por turma: MA6G1, MA6G2...
            df_count = get_df("SELECT COUNT(*) AS cnt FROM groups WHERE turma=:t", t=base_turma)
            next_num = int(df_count["cnt"].iloc[0]) + 1
            group_code = f"{base_turma}G{next_num}"  # << corrigido (antes estava G{n}{turma})
            exec_sql("""INSERT INTO groups(code,turma,course_code,created_by,created_at)
                        VALUES(:c,:t,:cc,:u,:ts)""",
                     c=group_code, t=base_turma, cc=course_val, u=created_by_val,
                     ts=datetime.now().isoformat(timespec="seconds"))
            st.success(f"Grupo {group_code} criado.")
        except Exception as e:
            st.error(f"Erro ao criar: {e}")

    st.markdown("---")
    st.subheader("Adicionar membros (5–6)")
    gdf = list_groups_user()
    if gdf.empty:
        st.info("Crie um grupo primeiro.")
    else:
        sel_group = st.selectbox("Selecione o grupo", gdf["code"].tolist(), key="sel_group_members")
        members = group_details(sel_group)

        # filtro por turma (para autocomplete)
        turmas = ["Todas","MA6","MB6","NA6","NB6"]
        turma_filtro = st.selectbox("Filtrar por turma", turmas, index=0, key="filtro_turma_add")
        if turma_filtro == "Todas":
            cand = get_df("SELECT name FROM students ORDER BY name")["name"].tolist()
        else:
            cand = get_df("SELECT name FROM students WHERE turma=:t ORDER BY name", t=turma_filtro)["name"].tolist()

        new_member = st.selectbox("Adicionar membro (digite para filtrar)",
                                  options=[""] + cand, key="add_member_select")

        colx, coly = st.columns(2)
        if colx.button("Adicionar ao grupo", key="btn_add_member"):
            if not (new_member or "").strip():
                st.error("Escolha um nome.")
            else:
                gid = int(gdf[gdf["code"] == sel_group]["id"].iloc[0])
                name_try = new_member.strip()
                ex = get_df("SELECT 1 FROM students WHERE name=:n", n=name_try)
                if ex.empty:
                    exec_sql("""INSERT INTO pending_students(name, turma, requester, requested_at)
                                VALUES(:n,:t,:r,:ts)""",
                             n=name_try,
                             t=turma_filtro if turma_filtro!="Todas" else "",
                             r=st.session_state.get("name") or "",
                             ts=datetime.now().isoformat(timespec="seconds"))
                    st.info("Aluno não cadastrado — pedido enviado aos docentes.")
                else:
                    try:
                        exec_sql("INSERT OR IGNORE INTO group_members(group_id,student_name) VALUES(:g,:n)",
                                 g=gid, n=name_try)
                        st.success("Membro adicionado.")
                    except Exception as e:
                        st.error(f"Erro ao adicionar: {e}")

        if coly.button("Remover último membro", key="btn_remove_last"):
            gid = int(gdf[gdf["code"] == sel_group]["id"].iloc[0])
            exec_sql("""DELETE FROM group_members
                        WHERE rowid IN (
                          SELECT rowid FROM group_members
                          WHERE group_id=:g ORDER BY rowid DESC LIMIT 1
                        )""", g=gid)
            st.warning("Último membro removido.")

        st.write("Membros atuais:", members, f"({len(members)}/6)")

    st.markdown("---")
    st.subheader("Reserva de tema (exclusiva)")
    if gdf.empty:
        st.info("Crie um grupo e adicione membros antes de reservar um tema.")
    else:
        sel_group2 = st.selectbox("Grupo para reservar", gdf["code"].tolist(), key="reserve_group")
        members2 = group_details(sel_group2)
        ok_res, why = can_reserve_theme_count(len(members2))
        if not ok_res:
            st.error(why)
        else:
            cat_res = st.selectbox("Filtrar por categoria",
                                   ["Todos","Privatização","Concessão","PPP","Financiamento/BNDES","Outro"],
                                   key="cat_res")
            free_list = list_free_themes(cat_res)
            theme_choice = st.selectbox("Temas disponíveis", free_list, key="theme_choice")
            cols = st.columns(2)
            if cols[0].button("Reservar tema", key="btn_reserve"):
                ok, msg = reserve_theme(theme_choice, sel_group2)
                st.success(msg) if ok else st.error(msg)
            my_reserved = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=sel_group2)["title"].tolist()
            release_sel = st.selectbox("Liberar tema reservado (do seu grupo)",
                                       my_reserved, key="release_sel") if my_reserved else None
            released_by = st.text_input("Seu nome (quem está liberando)", key="released_by")
            if cols[1].button("Liberar tema", key="btn_release"):
                if not release_sel:
                    st.error("Seu grupo não possui tema reservado.")
                else:
                    ok, msg = release_theme(release_sel, (released_by or "").strip())
                    st.warning(msg) if ok else st.error(msg)

    st.markdown("---")
    st.subheader("Status dos temas")
    cat_filter = st.selectbox("Categoria",
                              ["Todos","Privatização","Concessão","PPP","Financiamento/BNDES","Outro"],
                              key="cat_view")
    if cat_filter == "Todos":
        tdf = get_df("""SELECT number, title, category, status, reserved_by, reserved_at
                        FROM themes ORDER BY status DESC, number""")
    else:
        tdf = get_df("""SELECT number, title, category, status, reserved_by, reserved_at
                        FROM themes WHERE category=:c
                        ORDER BY status DESC, number""", c=cat_filter)
    st.dataframe(tdf, use_container_width=True)

# -------------------------------
# (2) UPLOAD
# -------------------------------
with tabs[1]:
    st.subheader("Upload de trabalhos finais")
    gdf = list_groups_user()
    if gdf.empty:
        st.info("Crie um grupo primeiro.")
    else:
        group = st.selectbox("Grupo", gdf["code"].tolist(), key="group_upload")
        tdf = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=group)
        theme = tdf["title"].iloc[0] if not tdf.empty else None
        if not theme:
            st.error("Este grupo ainda não reservou um tema.")
        else:
            st.write("Tema do grupo:", f"**{theme}**")
            report = st.file_uploader("Relatório (PDF)", type=["pdf"], key="up_report")
            slides = st.file_uploader("Apresentação (PPTX ou PDF)", type=["pptx","pdf"], key="up_slides")
            bundle = st.file_uploader("Materiais adicionais (ZIP)", type=["zip"], key="up_zip")
            video = st.text_input("Link do vídeo (YouTube, Stream, etc.)", key="up_video")
            consent = st.checkbox("Cedo os direitos patrimoniais à PUC-SP para divulgação acadêmica/extensionista, com crédito aos autores.", key="up_consent")
            submitted_by = st.text_input("Seu nome (quem está submetendo)", key="up_by")
            if st.button("Enviar", key="btn_submit"):
                if not consent:
                    st.error("É necessário marcar a cessão de direitos para enviar.")
                else:
                    gdir = os.path.join(UPLOAD_DIR, group.replace('/', '_'))
                    os.makedirs(gdir, exist_ok=True)
                    def save_file(up, name):
                        if up is None: return None
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
                             u=(submitted_by or "").strip(), ts=datetime.now().isoformat(timespec="seconds"))
                    st.success("Submissão recebida.")

    st.markdown("---")
    st.subheader("Submissões do seu grupo")
    if not gdf.empty:
        group2 = st.selectbox("Ver submissões do grupo", gdf["code"].tolist(), key="sub_view")
        sdf = get_df("""SELECT id, theme_title, report_path, slides_path, zip_path, video_link,
                       submitted_by, submitted_at, approved FROM submissions
                       WHERE group_code=:g ORDER BY submitted_at DESC""", g=group2)
        st.dataframe(sdf, use_container_width=True)

# -------------------------------
# (3) GALERIA/AVALIAÇÃO – DOCENTE
# -------------------------------
if st.session_state.get("user_type") == "teacher":
    with tabs[2]:
        st.subheader("Avaliação por docentes")
        email = st.text_input("E-mail institucional", key="rev_email")
        pin = st.text_input("PIN", type="password", key="rev_pin")
        ok = False
        if st.button("Entrar", key="btn_rev_login"):
            dfp = get_df("SELECT * FROM professors WHERE email=:e AND pin=:p", e=email, p=pin)
            ok = not dfp.empty
            st.success("Acesso ok.") if ok else st.error("Credenciais inválidas.")
        if ok:
            pr = get_df("SELECT id,name FROM professors WHERE email=:e", e=email)
            instr_id = pr["id"].iloc[0]
            sdf = get_df("""SELECT id, group_code, theme_title, submitted_at
                            FROM submissions WHERE approved=1
                            ORDER BY submitted_at DESC""")
            st.dataframe(sdf, use_container_width=True)
            sid = st.selectbox("Trabalho (ID)", sdf["id"].tolist() if not sdf.empty else [], key="rev_sid")
            like = st.toggle("Curtir", key="rev_like")
            score = st.slider("Nota", 0, 10, 8, key="rev_score")
            if st.button("Salvar avaliação", key="btn_save_review"):
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

    # ---------------------------
    # (4) ADMINISTRAÇÃO – DOCENTE
    # ---------------------------
    with tabs[3]:
        st.subheader("Aprovar submissões para galeria")
        sdf = get_df("SELECT id, group_code, theme_title, submitted_at, approved FROM submissions ORDER BY submitted_at DESC")
        st.dataframe(sdf, use_container_width=True)
        ids = st.multiselect("IDs para aprovar", sdf["id"].tolist(), key="approve_ids")
        if st.button("Aprovar selecionadas", key="btn_approve_subs"):
            for i in ids:
                exec_sql("UPDATE submissions SET approved=1 WHERE id=:i", i=int(i))
            st.success("Aprovadas.")

        st.markdown("---")
        st.subheader("Temas (liberar/travar, importar/atualizar)")
        tdf = get_df("SELECT number, title, category, status, reserved_by, reserved_at FROM themes ORDER BY number")
        st.dataframe(tdf, use_container_width=True)
        up_themes = st.file_uploader("Importar JSON de temas", type=["json"], key="themes_up")
        if up_themes and st.button("Carregar temas", key="btn_load_themes"):
            tmp = os.path.join(DATA_DIR, "_themes_upload.json")
            with open(tmp, "wb") as f:
                f.write(up_themes.read())
            addn = ensure_themes_from_json(tmp)
            st.success(f"Temas adicionados: {addn}. (itens existentes pelo título são ignorados)")

        st.markdown("---")
        st.subheader("Aprovar alunos pendentes")
        pend_df = get_df("SELECT id, name, turma, requester FROM pending_students ORDER BY requested_at DESC")
        if pend_df.empty:
            st.info("Não há alunos pendentes no momento.")
        else:
            st.dataframe(pend_df, use_container_width=True)
            sel_pend = st.multiselect("IDs para aprovar", pend_df["id"].tolist(), key="pend_ids")
            if st.button("Aprovar selecionados", key="btn_approve_pend"):
                with engine.begin() as conn:
                    for pid in sel_pend:
                        row = conn.execute(text("SELECT * FROM pending_students WHERE id=:i"),
                                           {"i": int(pid)}).mappings().first()
                        if row:
                            conn.execute(text("""INSERT OR IGNORE INTO students(ra, name, email, turma, active)
                                                 VALUES(:ra, :name, :email, :turma, 1)"""),
                                         {"ra": None, "name": row["name"], "email": None, "turma": row["turma"]})
                            conn.execute(text("DELETE FROM pending_students WHERE id=:i"), {"i": int(pid)})
                st.success("Alunos aprovados.")

    # ---------------------------
    # (5) ALUNOS & DOCENTES – DOCENTE
    # ---------------------------
    with tabs[4]:
        st.subheader("Importar alunos (CSV) — colunas: ra,name,email,turma")
        up_alunos = st.file_uploader("CSV de alunos", type=["csv"], key="csv_alunos")
        if up_alunos and st.button("Processar CSV", key="btn_proc_csv"):
            df = pd.read_csv(up_alunos)
            with engine.begin() as conn:
                for row in df.to_dict(orient="records"):
                    conn.execute(text("""INSERT OR IGNORE INTO students(ra,name,email,turma)
                                       VALUES(:ra,:name,:email,:turma)"""), row)
            st.success(f"{len(df)} aluno(s) processados.")

        st.markdown("---")
        st.subheader("Importar listas PUC (TXT) — múltiplos arquivos")
        term = st.text_input("Semestre (term)", value="2025/2", key="term_txt")
        up_txt = st.file_uploader("Arquivos .txt", type=["txt"], accept_multiple_files=True, key="txts")
        if up_txt and st.button("Processar TXT", key="btn_proc_txt"):
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
        sid = col_a.text_input("ID do aluno (coluna 'id') para alocar", key="alloc_sid")
        gcode = col_b.text_input("Grupo destino (ex.: MA6G1)", key="alloc_gcode")
        if st.button("Alocar aluno", key="btn_alloc"):
            try:
                link_student_to_group(int(sid), gcode.strip().upper())
                st.success("Aluno alocado.")
            except Exception as e:
                st.error(str(e))

        st.markdown("---")
        st.subheader("Docentes (PIN)")
        name = st.text_input("Nome", key="prof_name")
        email = st.text_input("E-mail", key="prof_email")
        role = st.selectbox("Papel", ["docente","admin"], key="prof_role")
        pinp = st.text_input("PIN", type="password", key="prof_pin")
        if st.button("Salvar docente", key="btn_save_prof"):
            exec_sql("""INSERT INTO professors(name,email,role,pin) VALUES(:n,:e,:r,:p)
                        ON CONFLICT(email) DO UPDATE SET name=:n, role=:r, pin=:p""",
                     n=name, e=email, r=role, p=pinp)
            st.success("Docente salvo/atualizado.")

# rodapé
st.caption("MVP – Submissões Industrial & EBC II (2º/2025)")
