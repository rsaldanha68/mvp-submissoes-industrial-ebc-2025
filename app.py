# app.py
import os, re, io, json, pathlib, uuid
from datetime import datetime, date
from typing import List, Dict, Optional

import streamlit as st
import pandas as pd
import requests
from sqlalchemy import create_engine, text

# =============== Config inicial ===============
st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

DATA_DIR   = "data"
UPLOAD_DIR = "uploads"   # usado sempre (cache local). Se Graph estiver configurado, também sobe para SharePoint.
PUBLIC_DIR = "public"

for p in (DATA_DIR, UPLOAD_DIR, PUBLIC_DIR):
    os.makedirs(p, exist_ok=True)

DB_URL = f"sqlite:///{os.path.join(DATA_DIR,'app.db')}"
engine = create_engine(DB_URL, future=True)

# Defaults de app (podem ser sobrescritos por secrets)
APP_TERM          = st.secrets.get("app", {}).get("TERM", "2025/2")
MIN_GROUP         = int(st.secrets.get("app", {}).get("MIN_GROUP", 5))
RESERVE_DEADLINE  = st.secrets.get("app", {}).get("RESERVE_DEADLINE", "2025-10-15T23:59:00")
PUBLISH_MIN_SCORE = float(st.secrets.get("app", {}).get("PUBLISH_MIN_SCORE", 7.0))

# Docentes semente
SEED_PROFESSORS = [
    # name, email, role, pin, approved
    ("ROLAND VERAS SALDANHA JUNIOR", "rsaldanha@pucsp.br", "admin", "8722", 1),
    ("MARCIA FLAIRE PEDROZA",        "marciapedroza@pucsp.br","docente","",    1),
    ("JULIO MANUEL PIRES",           "jmpires@pucsp.br",      "docente","",    1),
    ("Raphael Almeida Videira",      "ravideira@pucsp.br",    "docente","",    1),
    ("Tomas Bruginski de Paula",     "tbruginski@pucsp.br",   "docente","",    1),
]

# =============== DB bootstrap / migrações leves ===============
def _add_col(conn, table, coldef):
    try:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {coldef}")
    except Exception:
        pass  # já existe

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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        student_name TEXT NOT NULL
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
        media_link TEXT,         -- video ou audio
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
        course_code TEXT DEFAULT 'IND',  -- 'IND' ou 'EBCII'
        active INTEGER DEFAULT 1
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS professors(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        role TEXT CHECK (role IN ('admin','docente')) DEFAULT 'docente',
        pin TEXT,
        approved INTEGER DEFAULT 0
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL,
        instructor_id INTEGER NOT NULL,
        score REAL,
        liked INTEGER DEFAULT 0,
        c_report TEXT,
        c_slides TEXT,
        c_media TEXT,
        c_overall TEXT,
        created_at TEXT,
        UNIQUE(submission_id, instructor_id)
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS config(
        key TEXT PRIMARY KEY,
        value TEXT
    );""")

    # migrações leves (add columns se faltarem)
    _add_col(conn, "students", "course_code TEXT DEFAULT 'IND'")
    _add_col(conn, "submissions", "media_link TEXT")
    _add_col(conn, "professors", "approved INTEGER DEFAULT 0")
    _add_col(conn, "reviews", "c_report TEXT")
    _add_col(conn, "reviews", "c_slides TEXT")
    _add_col(conn, "reviews", "c_media TEXT")
    _add_col(conn, "reviews", "c_overall TEXT")

    # seeds
    for n, e, r, p, ap in SEED_PROFESSORS:
        conn.execute(text("""
            INSERT INTO professors(name,email,role,pin,approved)
            VALUES(:n,:e,:r,:p,:a)
            ON CONFLICT(email) DO UPDATE SET name=:n, role=:r
        """), {"n": n, "e": e.lower(), "r": r, "p": p, "a": ap})

    # config defaults
    def _set_default(k, v):
        conn.execute(text("""
            INSERT INTO config(key,value) VALUES(:k,:v)
            ON CONFLICT(key) DO UPDATE SET value=COALESCE(value,:v)
        """), {"k": k, "v": v})
    _set_default("TERM", APP_TERM)
    _set_default("MIN_GROUP", str(MIN_GROUP))
    _set_default("RESERVE_DEADLINE", RESERVE_DEADLINE)
    _set_default("PUBLISH_MIN_SCORE", str(PUBLISH_MIN_SCORE))

# =============== Helpers DB ===============
def cfg_get(key, cast=str):
    df = pd.read_sql(text("SELECT value FROM config WHERE key=:k"), engine, params={"k": key})
    if df.empty: return None
    v = df["value"].iloc[0]
    try:
        return cast(v)
    except Exception:
        return v

def get_df(sql: str, **params):
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params)

def exec_sql(sql: str, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)

# =============== Themes JSON loader ===============
def ensure_themes_from_json(path_json: str) -> int:
    if not os.path.exists(path_json): return 0
    with open(path_json, "r", encoding="utf-8") as f:
        items = json.load(f) or []
    # normaliza number 1..N se vier 0..N-1
    for i, it in enumerate(items, start=1):
        if not it.get("number"): it["number"] = i
        if it.get("number", 0) == 0: it["number"] = i
        if not it.get("category"): it["category"] = "Outro"
    inserted = 0
    with engine.begin() as conn:
        have = set(pd.read_sql("SELECT title FROM themes", conn)["title"].tolist() or [])
        for it in items:
            t = (it["title"] or "").strip()
            if not t or t in have: continue
            conn.execute(text("""
                INSERT INTO themes(number,title,category,status)
                VALUES(:n,:t,:c,'livre')
            """), {"n": int(it["number"]), "t": t, "c": it["category"]})
            inserted += 1
    return inserted

# carrega por padrão se existir
_added = ensure_themes_from_json(os.path.join("data","themes_2025_2.json"))
if _added:
    st.sidebar.success(f"Temas carregados: +{_added}")

# =============== Parser TXT PUC (embutido) ===============
RA_RE = re.compile(r"\b(\d{7,10})\b")
def parse_puc_txt_bytes(b: bytes) -> Dict:
    """Aceita um TXT exportado do sistema da PUC. Tenta extrair:
       disciplina (IND/EBCII), turma (MA6 etc), e lista de alunos (RA, Nome, Email)."""
    text = b.decode("utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    turma = ""
    disciplina = ""
    students = []

    # heurísticas simples
    for ln in lines[:40]:
        u = ln.upper()
        if "ECONOMIA INDUSTRIAL" in u:
            disciplina = "IND"
        if "ECONOMIA BRASILEIRA II" in u or "EBC II" in u:
            disciplina = "EBCII"
        m = re.search(r"\b(NA6|NB6|MA6|MB6)\b", u)
        if m: turma = m.group(1)

    # Linhas com RA + nome (+ email opcional)
    for ln in lines:
        m = RA_RE.search(ln)
        if not m: continue
        ra = m.group(1)
        # remove RA da linha e corta campos
        rest = ln.replace(ra, "").strip(" -;:|\t")
        # tenta separar por tab ou muitos espaços
        parts = re.split(r"[\t;|]{1,}|\s{2,}", rest)
        name = parts[0].strip() if parts else rest
        email = ""
        for p in parts[1:]:
            if "@" in p: email = p.strip(); break
        students.append({"ra": ra, "name": name, "email": email})
    return {"disciplina": disciplina or "IND", "turma": turma, "students": students}

def upsert_students_and_enroll(term: str, disc_code: str, turma: str, studs: List[Dict]):
    with engine.begin() as conn:
        # cria offering (disciplina+term+turma)
        did = pd.read_sql(text("SELECT 1"), conn) # dummy p/ garantir begin
        # garante disciplina
        conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS disciplines(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT)")
        conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('IND','Economia Industrial')"))
        conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('EBCII','Economia Brasileira II')"))
        # semesters/offers
        conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS offerings(id INTEGER PRIMARY KEY AUTOINCREMENT, discipline_id INTEGER, term TEXT, turma TEXT, instructor_id INTEGER, UNIQUE(discipline_id,term,turma))")
        # resolve discipline_id
        drow = conn.execute(text("SELECT id FROM disciplines WHERE code=:c"), {"c": disc_code}).fetchone()
        if not drow:
            drow = conn.execute(text("INSERT INTO disciplines(code,name) VALUES(:c,:n) RETURNING id"), {"c": disc_code, "n":"-" }).fetchone()
        discipline_id = int(drow[0])
        # resolve offering
        orow = conn.execute(text("""
            INSERT OR IGNORE INTO offerings(discipline_id,term,turma) VALUES(:d,:t,:u)
        """), {"d": discipline_id, "t": term, "u": turma})
        # cria tables se não existirem
        conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS enrollments(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, offering_id INTEGER, active INTEGER DEFAULT 1, UNIQUE(student_id,offering_id))")
        # pega offering_id
        ofr = conn.execute(text("SELECT id FROM offerings WHERE discipline_id=:d AND term=:t AND turma=:u"),
                           {"d": discipline_id, "t": term, "u": turma}).fetchone()
        offering_id = int(ofr[0])
        # students + enrollments
        for s in studs:
            ra = (s.get("ra") or "").strip()
            name = (s.get("name") or "").strip()
            email = (s.get("email") or "").strip().lower()
            if not ra or not name: continue
            conn.execute(text("""
                INSERT OR IGNORE INTO students(ra,name,email,turma,course_code,active)
                VALUES(:ra,:nm,:em,:tu,:cc,1)
            """), {"ra": ra, "nm": name, "em": email, "tu": turma, "cc": disc_code})
            srow = conn.execute(text("SELECT id FROM students WHERE ra=:ra"), {"ra": ra}).fetchone()
            sid = int(srow[0])
            conn.execute(text("""
                INSERT OR IGNORE INTO enrollments(student_id,offering_id,active) VALUES(:s,:o,1)
            """), {"s": sid, "o": offering_id})

# =============== OneDrive/SharePoint (opcional) ===============
def graph_is_configured() -> bool:
    a = st.secrets.get("aad", {})
    sp = st.secrets.get("sharepoint", {})
    return bool(a.get("TENANT_ID") and a.get("CLIENT_ID") and a.get("CLIENT_SECRET") and sp.get("SITE_ID") and sp.get("DRIVE_ID"))

def graph_token() -> Optional[str]:
    """Obtem token com client credentials (app permissions)."""
    a = st.secrets.get("aad", {})
    if not a: return None
    tenant = a.get("TENANT_ID"); client_id = a.get("CLIENT_ID"); secret = a.get("CLIENT_SECRET")
    if not (tenant and client_id and secret): return None
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    r = requests.post(url, data=data, timeout=20)
    if r.status_code != 200:
        return None
    return r.json().get("access_token")

def upload_to_sharepoint(local_path: str, remote_name: str) -> Optional[str]:
    if not graph_is_configured(): return None
    token = graph_token()
    if not token: return None
    sp = st.secrets.get("sharepoint", {})
    site = sp.get("SITE_ID"); drive = sp.get("DRIVE_ID"); folder = sp.get("FOLDER_PATH","Shared Documents")
    # cria pasta se necessário? Graph permite PUT direto com caminho
    url = f"https://graph.microsoft.com/v1.0/sites/{site}/drives/{drive}/root:/{folder}/{remote_name}:/content"
    with open(local_path, "rb") as f:
        r = requests.put(url, headers={"Authorization": f"Bearer {token}"}, data=f, timeout=120)
    if r.status_code in (200,201):
        item = r.json()
        # Tenta link de compartilhamento somente leitura
        link_url = f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item['id']}/createLink"
        body = {"type": "view", "scope": "organization"}  # restrito ao tenant
        lr = requests.post(link_url, headers={"Authorization": f"Bearer {token}", "Content-Type":"application/json"}, json=body, timeout=20)
        if lr.status_code in (200,201):
            return lr.json()["link"]["webUrl"]
        return item.get("webUrl")
    else:
        return None

# =============== Helpers de app ===============
def list_groups():
    return get_df("SELECT id, code, turma, course_code FROM groups ORDER BY turma, code")

def group_members(code: str) -> List[str]:
    dfm = get_df("""SELECT gm.student_name FROM group_members gm
                    JOIN groups g ON gm.group_id=g.id WHERE g.code=:c""", c=code)
    return dfm["student_name"].tolist() if not dfm.empty else []

def next_group_code(turma: str) -> str:
    df = get_df("SELECT code FROM groups WHERE turma=:t", t=turma)
    nums = []
    for c in df["code"].tolist():
        m = re.search(rf"^{re.escape(turma)}G(\d+)$", c)
        if m: nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"{turma}G{n}"

def reserve_theme(theme_title: str, group_code: str) -> (bool, str):
    now_iso = datetime.now().isoformat(timespec="seconds")
    # bloqueia antes de deadline com grupo < MIN_GROUP
    members = group_members(group_code)
    min_group = int(cfg_get("MIN_GROUP", int) or MIN_GROUP)
    deadline = cfg_get("RESERVE_DEADLINE", str) or RESERVE_DEADLINE
    allow = True
    if datetime.now() < datetime.fromisoformat(deadline) and len(members) < min_group:
        allow = False
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM themes WHERE title=:t"), {"t": theme_title}).fetchone()
        if not row or row[0] != "livre":
            return False, "Tema já reservado."
        if not allow:
            return False, f"Para reservar antes de {deadline}, o grupo precisa de pelo menos {min_group} membros."
        conn.execute(text("""UPDATE themes SET status='reservado', reserved_by=:g, reserved_at=:ts,
                          released_by=NULL, released_at=NULL WHERE title=:t"""),
                     {"g": group_code, "t": theme_title, "ts": now_iso})
    return True, "Reservado com sucesso."

def release_theme(theme_title: str, user: str):
    now_iso = datetime.now().isoformat(timespec="seconds")
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM themes WHERE title=:t"), {"t": theme_title}).fetchone()
        if not row or row[0] != "reservado":
            return False, "Tema não está reservado."
        conn.execute(text("""UPDATE themes SET status='livre', reserved_by=NULL, reserved_at=NULL,
                          released_by=:u, released_at=:ts WHERE title=:t"""),
                     {"u": user, "t": theme_title, "ts": now_iso})
    return True, "Tema liberado."

def students_unassigned():
    return get_df("""
        SELECT s.id, s.ra, s.name, s.turma, s.course_code
        FROM students s
        LEFT JOIN (
            SELECT DISTINCT gm.student_name AS name FROM group_members gm
        ) x ON x.name = s.name
        WHERE x.name IS NULL AND s.active=1
        ORDER BY s.turma, s.name
    """)

def link_student_to_group(student_id: int, group_code: str):
    gdf = list_groups()
    if gdf.empty: raise RuntimeError("Nenhum grupo cadastrado.")
    row = gdf[gdf["code"] == group_code]
    if row.empty: raise RuntimeError("Grupo não encontrado.")
    gid = int(row["id"].iloc[0])
    srow = get_df("SELECT name FROM students WHERE id=:i", i=int(student_id))
    if srow.empty: raise RuntimeError("Aluno não encontrado.")
    name = srow["name"].iloc[0]
    exec_sql("INSERT OR IGNORE INTO group_members(group_id,student_name) VALUES(:g,:n)", g=gid, n=name)

def list_free_themes(category: Optional[str] = None):
    if category and category != "Todos":
        df = get_df("SELECT title FROM themes WHERE status='livre' AND category=:c ORDER BY number", c=category)
    else:
        df = get_df("SELECT title FROM themes WHERE status='livre' ORDER BY number")
    return df["title"].tolist()

# =============== Sessão / Autenticação simples ===============
if "auth" not in st.session_state:
    st.session_state["auth"] = {"who": "anon"}  # anon | aluno | docente

st.sidebar.title("Acesso")
role_choice = st.sidebar.radio("Sou…", ["Aluno", "Docente"], horizontal=True)

if role_choice == "Aluno":
    ra = st.sidebar.text_input("RA")
    email_aluno = st.sidebar.text_input("E-mail (opcional)")
    if st.sidebar.button("Entrar", key="aluno_login"):
        # match por RA
        df = get_df("SELECT id, ra, name, email, turma FROM students WHERE ra=:ra AND active=1", ra=ra.strip())
        if df.empty:
            st.sidebar.error("RA não encontrado. Solicite inclusão ao docente.")
        else:
            st.session_state["auth"] = {
                "who": "aluno",
                "id": int(df["id"].iloc[0]),
                "ra": df["ra"].iloc[0],
                "name": df["name"].iloc[0],
                "email": df["email"].iloc[0] or email_aluno.strip(),
                "turma": df["turma"].iloc[0],
            }
            st.sidebar.success(f"Bem-vindo(a), {df['name'].iloc[0].split()[0]}!")

if role_choice == "Docente":
    email = st.sidebar.text_input("E-mail institucional")
    pin   = st.sidebar.text_input("PIN", type="password")
    if st.sidebar.button("Entrar", key="doc_login"):
        email_norm = (email or "").strip().lower()
        with engine.begin() as conn:
            prof = conn.execute(text("""
                SELECT id,name,email,role,pin,approved FROM professors WHERE LOWER(email)=:e
            """), {"e": email_norm}).fetchone()
            if not prof:
                st.sidebar.error("Conta de docente não encontrada. Solicite acesso ao admin.")
            elif int(prof["approved"]) != 1:
                st.sidebar.warning("Conta de docente pendente de aprovação.")
            elif (pin or "") != (prof["pin"] or ""):
                st.sidebar.error("PIN inválido.")
            else:
                st.session_state["auth"] = {
                    "who": "docente",
                    "id": int(prof["id"]),
                    "email": prof["email"],
                    "name": prof["name"],
                    "role": prof["role"],
                }
                st.sidebar.success("Login efetuado.")

if st.sidebar.button("Sair"):
    st.session_state["auth"] = {"who": "anon"}
    st.experimental_rerun()

auth = st.session_state["auth"]

# =============== UI Tabs (por perfil) ===============
def tab_grupos_temas():
    st.subheader("Grupos & Temas")
    colA, colB = st.columns([1,1])
    with colA:
        st.markdown("**Criar grupo**")
        turma_base = st.selectbox("Turma base", ["MA6","MB6","NA6","NB6"])
        iniciador  = st.text_input("Seu nome (iniciador do grupo)", value=(auth.get("name","") if auth["who"]=="aluno" else ""))
        if st.button("Gerar grupo automático"):
            code = next_group_code(turma_base)
            try:
                exec_sql("""INSERT INTO groups(code,turma,course_code,created_by,created_at)
                            VALUES(:c,:t,'JOINT',:u,:ts)""",
                         c=code, t=turma_base, u=iniciador.strip() or "—",
                         ts=datetime.now().isoformat(timespec="seconds"))
                st.success(f"Grupo criado: **{code}**")
            except Exception as e:
                st.error(f"Erro: {e}")

    with colB:
        st.markdown("**Adicionar membros (5–6)**")
        gdf = list_groups()
        if gdf.empty:
            st.info("Crie um grupo primeiro.")
        else:
            sel_group = st.selectbox("Grupo", gdf["code"].tolist(), key="add_mem_group")
            # dropdown por turma (auto-complete via selectbox com pesquisa)
            turma_filter = st.selectbox("Filtrar alunos pela turma", ["Todos","MA6","MB6","NA6","NB6"])
            if turma_filter == "Todos":
                adf = get_df("SELECT id, name, turma FROM students WHERE active=1 ORDER BY turma, name")
            else:
                adf = get_df("SELECT id, name, turma FROM students WHERE active=1 AND turma=:t ORDER BY name", t=turma_filter)
            # Input de busca
            q = st.text_input("Buscar aluno por nome (opcional)")
            if q:
                adf = adf[adf["name"].str.contains(q, case=False, na=False)]
            st.dataframe(adf.rename(columns={"id":"ID (use para alocar)"}), use_container_width=True, height=260)
            colx, coly = st.columns(2)
            sid = colx.text_input("ID do aluno para alocar")
            if coly.button("Alocar"):
                try:
                    link_student_to_group(int(sid), sel_group)
                    st.success("Aluno alocado.")
                except Exception as e:
                    st.error(str(e))

    st.markdown("---")
    st.markdown("**Reserva de tema (exclusiva)**")
    gdf = list_groups()
    if gdf.empty:
        st.info("Crie um grupo e adicione 5–6 membros antes de reservar tema.")
    else:
        sel_group2 = st.selectbox("Grupo para reservar", gdf["code"].tolist(), key="reserve_group")
        members2 = group_members(sel_group2)
        st.write("Membros:", ", ".join(members2) if members2 else "—", f"({len(members2)}/6)")
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
    st.markdown("**Status dos temas**")
    cat_filter = st.selectbox("Categoria", ["Todos","Privatização","Concessão","PPP","Financiamento/BNDES","Outro"], key="cat_view")
    if cat_filter == "Todos":
        tdf = get_df("SELECT number, title, category, status, reserved_by, reserved_at FROM themes ORDER BY status DESC, number")
    else:
        tdf = get_df("SELECT number, title, category, status, reserved_by, reserved_at FROM themes WHERE category=:c ORDER BY status DESC, number", c=cat_filter)
    st.dataframe(tdf, use_container_width=True)

def tab_upload():
    st.subheader("Upload de trabalhos (PDF/PPTX/ZIP + vídeo/áudio)")
    gdf = list_groups()
    if gdf.empty:
        st.info("Crie um grupo primeiro.")
        return
    group = st.selectbox("Grupo", gdf["code"].tolist())
    tdf = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=group)
    theme = tdf["title"].iloc[0] if not tdf.empty else None
    if not theme:
        st.error("Este grupo ainda não reservou um tema.")
        return
    st.write("Tema do grupo:", f"**{theme}**")
    report = st.file_uploader("Relatório (PDF)", type=["pdf"])
    slides = st.file_uploader("Apresentação (PPTX ou PDF)", type=["pptx","pdf"])
    bundle = st.file_uploader("Materiais adicionais (ZIP)", type=["zip"])
    media  = st.text_input("Link de vídeo/áudio (YouTube, Stream, OneDrive etc.)")
    consent = st.checkbox("Cedo os direitos patrimoniais à PUC-SP para divulgação acadêmica/extensionista, com crédito aos autores.")
    submitted_by = st.text_input("Seu nome (quem está submetendo)", value=(auth.get("name","") if auth["who"]=="aluno" else ""))

    if st.button("Enviar material"):
        if not consent:
            st.error("É necessário marcar a cessão de direitos para enviar.")
            return
        # salva local
        gdir = os.path.join(UPLOAD_DIR, group.replace('/','_'))
        os.makedirs(gdir, exist_ok=True)

        def save_local(up, name):
            if up is None: return None
            p = os.path.join(gdir, name)
            with open(p, "wb") as f: f.write(up.getbuffer())
            return p

        rpath = save_local(report, "relatorio.pdf")
        spath = save_local(slides, "apresentacao." + (slides.name.split('.')[-1] if slides else "pdf"))
        zpath = save_local(bundle, "materiais.zip")

        # se Graph estiver configurado, sobe cópias
        remote_report = remote_slides = remote_zip = None
        if graph_is_configured():
            if rpath: remote_report = upload_to_sharepoint(rpath, f"{group}_{uuid.uuid4().hex}_relatorio.pdf")
            if spath: remote_slides = upload_to_sharepoint(spath, f"{group}_{uuid.uuid4().hex}_apresentacao.{spath.split('.')[-1]}")
            if zpath: remote_zip    = upload_to_sharepoint(zpath, f"{group}_{uuid.uuid4().hex}_materiais.zip")

        exec_sql("""
            INSERT INTO submissions(group_code, theme_title, report_path, slides_path, zip_path, media_link,
                                    consent, submitted_by, submitted_at, approved)
            VALUES(:g,:t,:r,:s,:z,:m,1,:u,:ts,0)
        """, g=group, t=theme, r=remote_report or rpath, s=remote_slides or spath, z=remote_zip or zpath,
             m=media.strip(), u=submitted_by.strip(), ts=datetime.now().isoformat(timespec="seconds"))
        st.success("Submissão recebida. Ela aparecerá na área de avaliação para docentes.")

def tab_galeria_avaliacao():
    st.subheader("Avaliação (docentes)")
    if auth["who"] != "docente":
        st.info("Área exclusiva para docentes.")
        return
    sdf = get_df("""
        SELECT id, group_code, theme_title, submitted_at, approved
        FROM submissions ORDER BY submitted_at DESC
    """)
    st.dataframe(sdf, use_container_width=True, height=220)
    sid = st.selectbox("Escolha o ID da submissão para avaliar", sdf["id"].tolist() if not sdf.empty else [])
    if sid:
        sub = get_df("SELECT * FROM submissions WHERE id=:i", i=int(sid))
        if not sub.empty:
            st.markdown(f"**Grupo**: {sub['group_code'].iloc[0]}  |  **Tema**: {sub['theme_title'].iloc[0]}")
            st.markdown(f"**Enviado em**: {sub['submitted_at'].iloc[0]}  |  **Link mídia**: {sub['media_link'].iloc[0] or '—'}")
        like  = st.toggle("Curtir")
        score = st.slider("Nota (0–10)", 0.0, 10.0, 8.0, 0.5)
        c1, c2 = st.columns(2)
        c_report = c1.text_area("Comentários — Relatório")
        c_slides = c2.text_area("Comentários — Apresentação")
        c_media  = st.text_area("Comentários — Mídia (vídeo/áudio)")
        c_over   = st.text_area("Comentário geral")
        if st.button("Salvar avaliação"):
            exec_sql("""
                INSERT INTO reviews(submission_id, instructor_id, liked, score, c_report, c_slides, c_media, c_overall, created_at)
                VALUES(:i,:p,:l,:s,:cr,:cs,:cm,:co,:ts)
                ON CONFLICT(submission_id, instructor_id) DO UPDATE
                SET liked=:l, score=:s, c_report=:cr, c_slides=:cs, c_media=:cm, c_overall=:co, created_at=:ts
            """, i=int(sid), p=int(auth["id"]), l=int(like), s=float(score),
                 cr=c_report, cs=c_slides, cm=c_media, co=c_over,
                 ts=datetime.now().isoformat(timespec="seconds"))
            st.success("Avaliação registrada.")

    st.markdown("---")
    st.subheader("Métricas (para publicação)")
    m = get_df("""
        SELECT s.id, s.group_code, s.theme_title,
               ROUND(AVG(rv.score),2) AS media, SUM(rv.liked) AS likes, COUNT(rv.id) as avals
        FROM submissions s LEFT JOIN reviews rv ON rv.submission_id=s.id
        GROUP BY s.id
        ORDER BY media DESC, likes DESC
    """)
    st.dataframe(m, use_container_width=True)

def tab_admin():
    if auth["who"] != "docente":
        st.info("Área exclusiva para docentes.")
        return
    # Somente admin vê aprovações e config
    is_admin = (auth.get("role") == "admin")
    if is_admin:
        st.subheader("Configurações")
        term = st.text_input("Semestre (TERM)", value=cfg_get("TERM", str) or APP_TERM)
        min_group = st.number_input("Tamanho mínimo do grupo para reservar tema (MIN_GROUP)", 1, 10, int(cfg_get("MIN_GROUP", int) or MIN_GROUP))
        deadline = st.text_input("Data limite de reserva (RESERVE_DEADLINE, ISO)", value=cfg_get("RESERVE_DEADLINE", str) or RESERVE_DEADLINE)
        min_score = st.number_input("Nota mínima para publicação (PUBLISH_MIN_SCORE)", 0.0, 10.0, float(cfg_get("PUBLISH_MIN_SCORE", float) or PUBLISH_MIN_SCORE), 0.1)
        if st.button("Salvar configurações"):
            exec_sql("REPLACE INTO config(key,value) VALUES('TERM', :v)", v=term)
            exec_sql("REPLACE INTO config(key,value) VALUES('MIN_GROUP', :v)", v=str(min_group))
            exec_sql("REPLACE INTO config(key,value) VALUES('RESERVE_DEADLINE', :v)", v=deadline)
            exec_sql("REPLACE INTO config(key,value) VALUES('PUBLISH_MIN_SCORE', :v)", v=str(min_score))
            st.success("Configurações salvas.")

        st.markdown("---")
        st.subheader("Importar temas (JSON)")
        up_themes = st.file_uploader("JSON com [{number,title,category}...]", type=["json"])
        if up_themes and st.button("Carregar temas"):
            tmp = os.path.join(DATA_DIR, "_themes_upload.json")
            with open(tmp, "wb") as f: f.write(up_themes.read())
            addn = ensure_themes_from_json(tmp)
            st.success(f"Temas adicionados: {addn}. (títulos duplicados são ignorados)")

    st.markdown("---")
    st.subheader("Aprovar para vitrine pública (após avaliação)")
    publish_min = float(cfg_get("PUBLISH_MIN_SCORE", float) or PUBLISH_MIN_SCORE)
    cand = get_df(f"""
        SELECT s.id, s.group_code, s.theme_title,
               ROUND(AVG(rv.score),2) AS media, SUM(rv.liked) AS likes, COUNT(rv.id) AS avals,
               s.approved
        FROM submissions s LEFT JOIN reviews rv ON rv.submission_id=s.id
        GROUP BY s.id
        HAVING media >= {publish_min} AND avals >= 1
        ORDER BY approved ASC, media DESC
    """)
    st.dataframe(cand, use_container_width=True)
    ids = st.multiselect("IDs para aprovar (publicação externa)", cand[cand["approved"]==0]["id"].tolist())
    if st.button("Aprovar selecionadas"):
        for i in ids:
            exec_sql("UPDATE submissions SET approved=1 WHERE id=:i", i=int(i))
        st.success("Aprovadas para vitrine.")

    st.markdown("---")
    st.subheader("Docentes")
    ddf = get_df("SELECT id,name,email,role,approved FROM professors ORDER BY role DESC, name")
    st.dataframe(ddf, use_container_width=True)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        nm = st.text_input("Nome")
    with col2:
        em = st.text_input("E-mail")
    with col3:
        rl = st.selectbox("Papel", ["docente","admin"])
    with col4:
        pin = st.text_input("PIN (opcional)", type="password")
    apr = st.checkbox("Aprovado", value=True)
    if st.button("Salvar docente"):
        exec_sql("""
            INSERT INTO professors(name,email,role,pin,approved)
            VALUES(:n,:e,:r,:p,:a)
            ON CONFLICT(email) DO UPDATE SET name=:n, role=:r, pin=:p, approved=:a
        """, n=nm, e=em.lower(), r=rl, p=pin, a=int(apr))
        st.success("Docente salvo/atualizado.")

    st.markdown("---")
    st.subheader("Importar alunos")
    st.caption("CSV: colunas **ra,name,email,turma,course_code** (course_code = IND ou EBCII)")
    up_csv = st.file_uploader("CSV", type=["csv"])
    if up_csv and st.button("Processar CSV"):
        df = pd.read_csv(up_csv)
        with engine.begin() as conn:
            for row in df.to_dict(orient="records"):
                conn.execute(text("""
                  INSERT OR IGNORE INTO students(ra,name,email,turma,course_code,active)
                  VALUES(:ra,:name,:email,:turma,:course_code,1)
                """), row)
        st.success(f"{len(df)} alunos processados.")
    st.caption("TXT PUC: envie 1+ .txt exportados (detecta turma/curso)")
    up_txts = st.file_uploader("TXT(s)", type=["txt"], accept_multiple_files=True)
    if up_txts and st.button("Processar TXT"):
        ok = 0
        term = cfg_get("TERM", str) or APP_TERM
        for upl in up_txts:
            meta = parse_puc_txt_bytes(upl.read())
            turma = meta.get("turma") or ""
            disc  = meta.get("disciplina") or "IND"
            studs = meta.get("students") or []
            if not turma or not studs:
                st.warning(f"{upl.name}: não consegui detectar turma ou alunos. Ignorado.")
                continue
            upsert_students_and_enroll(term, disc, turma, studs)
            ok += 1
        st.success(f"TXT processados: {ok}")

    st.markdown("---")
    st.subheader("Alunos sem grupo → alocar")
    sdf = students_unassigned()
    st.dataframe(sdf, use_container_width=True, height=240)
    colA, colB = st.columns(2)
    sid = colA.text_input("ID do aluno")
    gcode = colB.text_input("Grupo (ex.: MA6G1)")
    if st.button("Alocar aluno"):
        try:
            link_student_to_group(int(sid), gcode.strip().upper())
            st.success("Aluno alocado.")
        except Exception as e:
            st.error(str(e))

    st.markdown("---")
    st.subheader("Relatórios")
    rpt_choice = st.selectbox("Relatório", [
        "Alunos por turma e curso",
        "Grupos e membros",
        "Temas e status",
        "Avaliações por docente",
    ])
    if st.button("Gerar relatório"):
        if rpt_choice == "Alunos por turma e curso":
            r = get_df("""
                SELECT turma, course_code, COUNT(*) AS total
                FROM students WHERE active=1
                GROUP BY turma, course_code ORDER BY turma, course_code
            """)
            st.dataframe(r, use_container_width=True)
        elif rpt_choice == "Grupos e membros":
            r = get_df("""
                SELECT g.turma, g.code, GROUP_CONCAT(gm.student_name, ' / ') AS membros
                FROM groups g LEFT JOIN group_members gm ON gm.group_id=g.id
                GROUP BY g.id ORDER BY g.turma, g.code
            """)
            st.dataframe(r, use_container_width=True)
        elif rpt_choice == "Temas e status":
            r = get_df("""
                SELECT number, title, category, status, reserved_by, reserved_at
                FROM themes ORDER BY status DESC, number
            """)
            st.dataframe(r, use_container_width=True)
        elif rpt_choice == "Avaliações por docente":
            r = get_df("""
                SELECT p.name AS docente, s.group_code, s.theme_title, ROUND(rv.score,2) AS nota, rv.liked,
                       rv.c_overall AS comentario, rv.created_at
                FROM reviews rv
                JOIN professors p ON p.id=rv.instructor_id
                JOIN submissions s ON s.id=rv.submission_id
                ORDER BY p.name, rv.created_at DESC
            """)
            st.dataframe(r, use_container_width=True)

def tab_aluno_info():
    st.subheader("Meu cadastro")
    if auth["who"] != "aluno":
        st.info("Entre como aluno (RA) na barra lateral.")
        return
    st.write(f"**RA**: {auth['ra']}  |  **Nome**: {auth['name']}  |  **Turma**: {auth['turma']}")
    st.markdown("> Use as abas **Grupos & Temas** e **Upload** para participar.")

# =============== Render por perfil ===============
st.title("Submissões – Industrial & EBC II (2º/2025)")

if auth["who"] == "docente":
    tabs = st.tabs(["📋 Grupos & Temas", "⬆️ Upload", "🧪 Galeria/Avaliação", "🛠 Admin", "👥 Aluno (visual)"])
    with tabs[0]: tab_grupos_temas()
    with tabs[1]: tab_upload()
    with tabs[2]: tab_galeria_avaliacao()
    with tabs[3]: tab_admin()
    with tabs[4]: tab_aluno_info()

elif auth["who"] == "aluno":
    tabs = st.tabs(["📋 Grupos & Temas", "⬆️ Upload", "👤 Meu cadastro"])
    with tabs[0]: tab_grupos_temas()
    with tabs[1]: tab_upload()
    with tabs[2]: tab_aluno_info()

else:
    st.info("Faça login na barra lateral (Aluno por RA, Docente por e-mail + PIN).")
    st.markdown("---")
    st.subheader("Demonstração (somente leitura)")
    st.caption("Você poderá ver as seções após entrar.")
