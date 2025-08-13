# -*- coding: utf-8 -*-
import os, json, re, pathlib, io, math, mimetypes, time
from datetime import datetime, date

import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text

# -----------------------------
# Config / Constantes
# -----------------------------
st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

APP_TERM_DEFAULT = "2025/2"
THEMES_JSON_DEFAULT = os.path.join("data", "themes_2025_2.json")

# Datas de governança (podem ser ajustadas em Admin)
DEFAULT_THEME_LOCK_DATE = os.environ.get("THEME_LOCK_DATE", "2025-05-15")  # até essa data exige 5–6 alunos p/ reservar tema

# Perfis
ROLE_ADMIN = "admin"
ROLE_DOCENTE = "docente"
ROLE_ALUNO = "aluno"

# Pastas
DATA_DIR = "data"
UPLOAD_DIR = "uploads"
PUBLIC_DIR = "public"
for p in (DATA_DIR, UPLOAD_DIR, PUBLIC_DIR):
    os.makedirs(p, exist_ok=True)

# Banco
DB_URL = f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}"
engine = create_engine(DB_URL, future=True)

# Credenciais “explícitas” p/ 1º login do dono (você)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "rsaldanha@pucsp.br")
ADMIN_PIN   = os.environ.get("ADMIN_PIN",   "8722")

# Seeds de docentes (podem logar depois que aprovados)
SEED_PROFESSORS = [
    # name, email, role, pin, approved
    ("Roland Veras Saldanha Junior", "rsaldanha@pucsp.br", ROLE_ADMIN, "8722", 1),
    ("Marcia Flaire Pedroza",        "marciapedroza@pucsp.br", ROLE_DOCENTE, "8722", 1),
    ("Julio Manuel Pires",           "jmpires@pucsp.br", ROLE_DOCENTE, "8722", 1),
    ("Raphael Almeida Videira",      "ravideira@pucsp.br", ROLE_DOCENTE, "8722", 1),
    ("Tomas Bruginski de Paula",     "tbruginski@pucsp.br", ROLE_DOCENTE, "8722", 1),
]

# -----------------------------
# Tabelas
# -----------------------------
with engine.begin() as conn:
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS config(
        key TEXT PRIMARY KEY,
        val TEXT
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS groups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        turma TEXT,
        course_code TEXT DEFAULT 'IND', -- 'IND' ou 'EBCII' (para docentes controlarem)
        created_by TEXT,
        created_at TEXT
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS group_members(
        group_id INTEGER,
        ra TEXT,
        student_name TEXT,
        turma TEXT,
        UNIQUE(group_id, student_name)
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS themes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number INTEGER,
        title TEXT UNIQUE,
        category TEXT,
        status TEXT CHECK(status IN ('livre','reservado')) DEFAULT 'livre',
        reserved_by TEXT,
        reserved_at TEXT,
        released_by TEXT,
        released_at TEXT
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS submissions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_code TEXT,
        theme_title TEXT,
        report_path TEXT,
        slides_path TEXT,
        zip_path TEXT,
        video_path TEXT,
        audio_path TEXT,
        video_link TEXT,
        consent INTEGER DEFAULT 0,
        submitted_by TEXT,
        submitted_at TEXT,
        stage TEXT CHECK(stage IN ('avaliacao','publicado')) DEFAULT 'avaliacao'
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL,
        instructor_id INTEGER NOT NULL,
        score REAL,
        liked INTEGER DEFAULT 0,
        comments TEXT,
        created_at TEXT,
        UNIQUE(submission_id, instructor_id)
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS students(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ra TEXT UNIQUE,
        name TEXT NOT NULL,
        email TEXT,
        turma TEXT,
        active INTEGER DEFAULT 1
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS professors(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        role TEXT CHECK(role IN ('admin','docente')) DEFAULT 'docente',
        pin TEXT,
        approved INTEGER DEFAULT 0
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS disciplines(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT UNIQUE, name TEXT NOT NULL
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS semesters(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      term TEXT UNIQUE NOT NULL
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS offerings(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      discipline_id INTEGER NOT NULL,
      term TEXT NOT NULL,
      turma TEXT NOT NULL,
      instructor_id INTEGER,
      UNIQUE(discipline_id, term, turma)
    )""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS enrollments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id INTEGER NOT NULL,
      offering_id INTEGER NOT NULL,
      active INTEGER DEFAULT 1,
      UNIQUE(student_id, offering_id)
    )""")
    # seeds
    conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('IND','Economia Industrial')"))
    conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('EBCII','Economia Brasileira II')"))
    conn.execute(text("INSERT OR IGNORE INTO semesters(term) VALUES(:t)"), {"t": APP_TERM_DEFAULT})
    # lock date default
    conn.execute(text("INSERT OR IGNORE INTO config(key,val) VALUES('theme_lock_date', :d)"), {"d": DEFAULT_THEME_LOCK_DATE})
    # seed professors (idempotente)
    for n,e,r,p,ap in SEED_PROFESSORS:
        conn.execute(text("""
            INSERT OR IGNORE INTO professors(name,email,role,pin,approved)
            VALUES(:n,:e,:r,:p,:a)
        """), {"n":n,"e":e,"r":r,"p":p,"a":ap})

# -----------------------------
# Helpers BD
# -----------------------------
def cfg_get(key:str, default:str=""):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT val FROM config WHERE key=:k"), {"k": key}).fetchone()
        return row[0] if row else default

def cfg_set(key:str, val:str):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO config(key,val) VALUES(:k,:v) ON CONFLICT(key) DO UPDATE SET val=:v"),
                     {"k": key, "v": val})

def get_df(sql:str, **params):
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params)

def exec_sql(sql:str, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)

# -----------------------------
# Temas (merge de JSON)
# -----------------------------
def ensure_themes_from_json(path_json:str) -> int:
    if not os.path.exists(path_json):
        return 0
    with open(path_json, "r", encoding="utf-8") as f:
        items = json.load(f) or []
    inserted = 0
    with engine.begin() as conn:
        existing = pd.read_sql("SELECT title FROM themes", conn)
        have = set(existing["title"].tolist()) if not existing.empty else set()
        for it in items:
            title = (it.get("title") or "").strip()
            if not title or title in have:
                continue
            conn.execute(text("""INSERT INTO themes(number,title,category,status)
                                 VALUES(:n,:t,:c,'livre')"""),
                         {"n": int(it.get("number",0) or 0),
                          "t": title,
                          "c": it.get("category","Outro")})
            inserted += 1
    return inserted

_ = ensure_themes_from_json(THEMES_JSON_DEFAULT)

# -----------------------------
# Sugestão de código de grupo
# -----------------------------
def next_group_code_for_turma(turma:str)->str:
    gdf = get_df("SELECT code FROM groups WHERE turma=:t", t=turma)
    nums = []
    for c in gdf["code"].tolist():
        m = re.search(r"[Gg](\d+)$", c)
        if m: nums.append(int(m.group(1)))
    nxt = (max(nums)+1) if nums else 1
    return f"{turma.upper()}G{nxt}"

def suggest_group_code_by_members_turma(member_turmas:list[str], creator_turma:str)->str:
    if not member_turmas: return next_group_code_for_turma(creator_turma)
    # maioria
    counts={}
    for t in member_turmas:
        if not t: continue
        counts[t]=counts.get(t,0)+1
    if not counts:
        base = creator_turma
    else:
        maxv = max(counts.values())
        cands = [t for t,v in counts.items() if v==maxv]
        base = creator_turma if creator_turma in cands else sorted(cands)[0]
    return next_group_code_for_turma(base)

# -----------------------------
# Reserva / Liberação de tema
# -----------------------------
def list_free_themes(category=None):
    if category and category!="Todos":
        df = get_df("SELECT title FROM themes WHERE status='livre' AND category=:c ORDER BY number", c=category)
    else:
        df = get_df("SELECT title FROM themes WHERE status='livre' ORDER BY number")
    return df["title"].tolist()

def reserve_theme(theme_title:str, group_code:str):
    lock_date = cfg_get("theme_lock_date", DEFAULT_THEME_LOCK_DATE)
    must_have = 5 if date.today() <= date.fromisoformat(lock_date) else 1
    members = group_details(group_code)
    if len(members) < must_have:
        return False, f"Até {lock_date} o grupo precisa ter ≥{must_have} membros para reservar tema."
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM themes WHERE title=:t"), {"t": theme_title}).fetchone()
        if not row or row[0] != "livre":
            return False, "Tema já reservado."
        conn.execute(text("""UPDATE themes SET status='reservado', reserved_by=:g, reserved_at=:ts,
                             released_by=NULL, released_at=NULL WHERE title=:t"""),
                     {"g": group_code, "t": theme_title, "ts": datetime.now().isoformat(timespec="seconds")})
    return True, "Tema reservado."

def release_theme(theme_title:str, user:str):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM themes WHERE title=:t"), {"t": theme_title}).fetchone()
        if not row or row[0] != "reservado":
            return False, "Tema não está reservado."
        conn.execute(text("""UPDATE themes SET status='livre', reserved_by=NULL, reserved_at=NULL,
                             released_by=:u, released_at=:ts WHERE title=:t"""),
                     {"u": user, "t": theme_title, "ts": datetime.now().isoformat(timespec="seconds")})
    return True, "Tema liberado."

# -----------------------------
# Grupos / Alocação
# -----------------------------
def list_groups():
    return get_df("SELECT id, code, turma, course_code FROM groups ORDER BY turma, code")

def group_details(code:str):
    dfm = get_df("""SELECT gm.student_name FROM group_members gm
                    JOIN groups g ON gm.group_id=g.id WHERE g.code=:c""", c=code)
    return dfm["student_name"].tolist() if not dfm.empty else []

def students_unassigned():
    return get_df("""SELECT s.id, s.ra, s.name, s.turma FROM students s
                      LEFT JOIN (SELECT DISTINCT gm.student_name AS name FROM group_members gm) x
                        ON x.name = s.name
                    WHERE x.name IS NULL
                    ORDER BY s.turma, s.name""")

def link_student_to_group(student_id:int, group_code:str):
    gdf = list_groups()
    if gdf.empty: raise RuntimeError("Nenhum grupo cadastrado.")
    row = gdf[gdf["code"]==group_code]
    if row.empty: raise RuntimeError("Grupo não encontrado.")
    gid = int(row["id"].iloc[0])
    srow = get_df("SELECT name,ra,turma FROM students WHERE id=:i", i=int(student_id))
    if srow.empty: raise RuntimeError("Aluno não encontrado.")
    name = srow["name"].iloc[0]; ra = srow["ra"].iloc[0]; turma = srow["turma"].iloc[0]
    exec_sql("INSERT OR IGNORE INTO group_members(group_id,ra,student_name,turma) VALUES(:g,:ra,:n,:t)",
             g=gid, ra=ra, n=name, t=turma)

# -----------------------------
# Upload (local + Graph/SharePoint)
# -----------------------------
def save_uploaded_file(folder:str, uploaded_file, filename:str) -> str|None:
    if not uploaded_file: return None
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return path

def graph_enabled()->bool:
    s = st.secrets.get("graph", {})
    return bool(s.get("tenant_id") and s.get("client_id") and s.get("client_secret"))

def graph_upload_large(local_path:str, remote_folder:str, remote_name:str)->str|None:
    """
    Envia para OneDrive/SharePoint usando Graph, chunked upload.
    Requer em st.secrets['graph']: tenant_id, client_id, client_secret, drive_id (ou site_id+drive_id), base_path
    Retorna a webUrl do arquivo (se OK).
    """
    try:
        import requests, msal
    except Exception:
        st.warning("Pacotes 'msal' e 'requests' são necessários para espelhar no SharePoint (veja requirements.txt).")
        return None

    g = st.secrets.get("graph", {})
    tenant = g["tenant_id"]; client = g["client_id"]; secret = g["client_secret"]
    drive_id = g.get("drive_id")  # recomendável informar diretamente
    base_path = g.get("base_path", "/")  # ex: /Submissoes/2025-2

    # token
    authority = f"https://login.microsoftonline.com/{tenant}"
    app = msal.ConfidentialClientApplication(client_id=client, client_credential=secret, authority=authority)
    scope = ["https://graph.microsoft.com/.default"]
    token = app.acquire_token_silent(scopes=scope, account=None) or app.acquire_token_for_client(scopes=scope)
    if "access_token" not in token:
        st.error(f"Graph token error: {token}")
        return None
    headers = {"Authorization": f"Bearer {token['access_token']}"}

    # cria pasta se necessário (mkdir recursivo simples)
    def ensure_folder(path_parts:list[str]):
        cur = ""
        for part in path_parts:
            cur = f"{cur}/{part}" if cur else part
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{cur}"
            r = requests.get(url, headers=headers)
            if r.status_code == 404:
                r = requests.post(f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children",
                                  headers={**headers, "Content-Type":"application/json"},
                                  json={"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
                if r.status_code >= 300:
                    st.error(f"Falha ao criar pasta {cur}: {r.text}")
                    return False
            elif r.status_code >= 300:
                st.error(f"Erro Graph: {r.text}")
                return False
        return True

    # garante /base_path/remote_folder
    relpath = base_path.strip("/").split("/") if base_path.strip("/") else []
    if remote_folder.strip("/"):
        relpath += remote_folder.strip("/").split("/")
    if not ensure_folder(relpath): return None
    folder_path = "/".join(relpath)

    # inicia upload session
    upload_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{folder_path}/{remote_name}:/createUploadSession"
    r = requests.post(upload_url, headers=headers, json={"@microsoft.graph.conflictBehavior":"replace"})
    if r.status_code >= 300:
        st.error(f"UploadSession erro: {r.text}")
        return None
    session = r.json()
    upurl = session["uploadUrl"]

    # envia em chunks
    CHUNK = 10*1024*1024
    size = os.path.getsize(local_path)
    with open(local_path, "rb") as f:
        start = 0
        while start < size:
            end = min(start+CHUNK, size)-1
            chunk = f.read(end-start+1)
            rr = requests.put(upurl, headers={
                "Content-Length": str(end-start+1),
                "Content-Range": f"bytes {start}-{end}/{size}"
            }, data=chunk)
            if rr.status_code in (200,201):
                item = rr.json()
                return item.get("webUrl")
            elif rr.status_code not in (202,):
                st.error(f"Falha no chunk ({start}-{end}): {rr.text}")
                return None
            start = end+1
    return None

# -----------------------------
# Autenticação
# -----------------------------
if "auth" not in st.session_state:
    st.session_state["auth"] = None

def logout():
    st.session_state["auth"] = None
    st.experimental_rerun()

def login_ui():
    st.header("Login")
    who = st.radio("Sou:", ["Aluno","Docente"], horizontal=True)
    email = st.text_input("E-mail institucional")
    pin   = st.text_input("PIN", type="password")

    if st.button("Entrar"):
        email_norm = (email or "").strip().lower()

        with engine.begin() as conn:
            prof = conn.execute(
                text("SELECT id,name,email,role,pin,approved FROM professors WHERE LOWER(email)=:e"),
                {"e": email_norm}
            ).fetchone()

            # auto-provisiona admin (primeiro login)
            if not prof and email_norm == ADMIN_EMAIL.lower():
                conn.execute(text("""
                    INSERT INTO professors(name,email,role,pin,approved)
                    VALUES(:n,:e,'admin',:p,1)
                """), {"n":"Administrador","e":email_norm,"p": pin or ADMIN_PIN})
                prof = conn.execute(
                    text("SELECT id,name,email,role,pin,approved FROM professors WHERE LOWER(email)=:e"),
                    {"e": email_norm}
                ).fetchone()

        if who=="Docente":
            if not prof:
                st.error("Conta de docente não encontrada. Solicite acesso ao administrador.")
                return
            if int(prof["approved"]) != 1:
                st.warning("Conta de docente pendente de aprovação.")
                return
            if (pin or "") != (prof["pin"] or ""):
                st.error("PIN inválido.")
                return
            st.session_state["auth"] = {
                "who":"docente","id":int(prof["id"]),"email":prof["email"],
                "name":prof["name"],"role":prof["role"]
            }
            st.success("Login efetuado.")
            st.experimental_rerun()
        else:
            # Aluno entra com email/RA qualquer – se existir em 'students' ganha experiência personalizada
            srow = get_df("SELECT id,ra,name,turma,email FROM students WHERE LOWER(email)=:e", e=email_norm)
            if srow.empty:
                st.info("E-mail não localizado nos alunos. Você pode seguir, mas pode ter acesso reduzido.")
                st.session_state["auth"] = {"who":"aluno","email":email_norm, "name":email_norm.split("@")[0]}
            else:
                r = srow.iloc[0]
                st.session_state["auth"] = {
                    "who":"aluno","id":int(r["id"]),"ra":r["ra"],"name":r["name"],"email":r["email"],"turma":r["turma"]
                }
            st.experimental_rerun()

# -----------------------------
# UI – Páginas
# -----------------------------
def page_grupos_temas(auth):
    st.subheader("Criar grupo (5–6 alunos)")
    c1,c2,c3 = st.columns(3)
    with c1:
        turma_creator = st.selectbox("Sua turma (para sugestão)", ["MA6","MB6","NA6","NB6"], key="turma_creator")
    with c2:
        # sugestão dinâmica
        member_hint = st.text_input("Membros (nomes separados por ; ) para sugerir turma")
        turmas_membros = []
        if member_hint.strip():
            # tenta achar turmas dos nomes informados
            names = [x.strip() for x in member_hint.split(";") if x.strip()]
            if names:
                q = "SELECT name,turma FROM students WHERE name IN (%s)" % ",".join([f":n{i}" for i in range(len(names))])
                params = {f"n{i}":names[i] for i in range(len(names))}
                df = get_df(q, **params)
                turmas_membros = df["turma"].dropna().tolist()
        sug = suggest_group_code_by_members_turma(turmas_membros, turma_creator)
        code = st.text_input("Código do grupo (ex.: MA6G1)", value=sug)
    with c3:
        created_by = st.text_input("Seu nome")
        disc = st.selectbox("Disciplina do grupo", ["IND","EBCII"])

    if st.button("Criar grupo"):
        if not code or not turma_creator:
            st.error("Informe turma e código.")
        elif not code.upper().startswith(turma_creator):
            st.error("O código deve começar com a turma (ex.: MA6G1).")
        else:
            try:
                exec_sql("""INSERT INTO groups(code,turma,course_code,created_by,created_at)
                            VALUES(:c,:t,:cc,:u,:ts)""",
                         c=code.strip().upper(), t=turma_creator, cc=disc, u=created_by.strip(),
                         ts=datetime.now().isoformat(timespec="seconds"))
                st.success("Grupo criado.")
            except Exception as e:
                st.error(f"Erro ao criar: {e}")

    st.markdown("---")
    st.subheader("Adicionar membros (5–6)")
    gdf = list_groups()
    if gdf.empty:
        st.info("Crie um grupo primeiro.")
    else:
        sel_group = st.selectbox("Selecione o grupo", gdf["code"].tolist())
        # busca alunos (autocomplete simples por turma)
        turma_filtro = st.selectbox("Filtrar por turma", ["Todas","MA6","MB6","NA6","NB6"], key="tflt")
        if turma_filtro=="Todas":
            alunos_df = get_df("SELECT id, name, turma FROM students ORDER BY turma, name")
        else:
            alunos_df = get_df("SELECT id, name, turma FROM students WHERE turma=:t ORDER BY name", t=turma_filtro)
        st.dataframe(alunos_df, use_container_width=True, height=240)
        sid = st.text_input("ID para alocar", key="sid_alloc")
        cols = st.columns(2)
        if cols[0].button("Alocar"):
            try:
                link_student_to_group(int(sid), sel_group.strip().upper())
                st.success("Aluno alocado.")
            except Exception as e:
                st.error(str(e))
        if cols[1].button("Remover último membro"):
            gid = int(gdf[gdf["code"]==sel_group]["id"].iloc[0])
            exec_sql("DELETE FROM group_members WHERE rowid IN (SELECT rowid FROM group_members WHERE group_id=:g ORDER BY rowid DESC LIMIT 1)", g=gid)
            st.warning("Último membro removido.")
        st.write("Membros atuais:", group_details(sel_group))

    st.markdown("---")
    st.subheader("Reserva de tema (exclusiva)")
    if gdf.empty:
        st.info("Crie um grupo e adicione membros antes de reservar tema.")
    else:
        sel_group2 = st.selectbox("Grupo para reservar", gdf["code"].tolist(), key="reserve_group")
        members2 = group_details(sel_group2)
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

def page_upload(auth):
    st.subheader("Upload de trabalhos (entrarão em **avaliação**)")
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
    slides = st.file_uploader("Apresentação (PPT/PPTX/PDF)", type=["ppt","pptx","pdf"])
    bundle = st.file_uploader("Arquivos adicionais (ZIP)", type=["zip"])
    video  = st.file_uploader("Vídeo (MP4/MOV/MKV) — 15 min ref.", type=["mp4","mov","mkv"])
    audio  = st.file_uploader("Áudio/Podcast (MP3/WAV/M4A)", type=["mp3","wav","m4a"])
    video_link = st.text_input("Link de vídeo (YouTube/Stream) — opcional")
    consent = st.checkbox("Cedo os direitos patrimoniais à PUC-SP para divulgação acadêmica/extensionista, com crédito aos autores.")
    submitted_by = st.text_input("Seu nome (quem está submetendo)")

    if st.button("Enviar"):
        if not consent:
            st.error("É necessário marcar a cessão de direitos para enviar.")
            return
        gdir = os.path.join(UPLOAD_DIR, group.replace('/', '_'))
        rpath = save_uploaded_file(gdir, report, "relatorio.pdf")
        spath = save_uploaded_file(gdir, slides, "apresentacao." + (slides.name.split('.')[-1] if slides else "pdf"))
        zpath = save_uploaded_file(gdir, bundle, "materiais.zip")
        vpath = save_uploaded_file(gdir, video,  "video." + (video.name.split('.')[-1] if video else ""))
        apath = save_uploaded_file(gdir, audio,  "audio." + (audio.name.split('.')[-1] if audio else ""))

        # espelha no SharePoint (se configurado)
        web_urls = {}
        if graph_enabled():
            remote_folder = f"{APP_TERM_DEFAULT}/{group}"
            if rpath: web_urls["report"] = graph_upload_large(rpath, remote_folder, os.path.basename(rpath))
            if spath: web_urls["slides"] = graph_upload_large(spath, remote_folder, os.path.basename(spath))
            if zpath: web_urls["zip"]    = graph_upload_large(zpath,  remote_folder, os.path.basename(zpath))
            if vpath: web_urls["video"]  = graph_upload_large(vpath,  remote_folder, os.path.basename(vpath))
            if apath: web_urls["audio"]  = graph_upload_large(apath,  remote_folder, os.path.basename(apath))

        exec_sql("""INSERT INTO submissions(group_code, theme_title, report_path, slides_path, zip_path,
                    video_path, audio_path, video_link, consent, submitted_by, submitted_at, stage)
                    VALUES(:g,:t,:r,:s,:z,:vp,:ap,:vl,:c,:u,:ts,'avaliacao')""",
                 g=group, t=theme, r=rpath, s=spath, z=zpath, vp=vpath, ap=apath, vl=video_link,
                 c=1 if consent else 0, u=submitted_by.strip(), ts=datetime.now().isoformat(timespec="seconds"))
        st.success("Submissão recebida e enviada para **avaliação**.")
        if web_urls:
            st.info("Links espelhados no SharePoint:")
            st.json(web_urls)

    st.markdown("---")
    st.subheader("Submissões do seu grupo")
    sdf = get_df("""SELECT id, theme_title, report_path, slides_path, zip_path, video_path, audio_path, video_link,
                    submitted_by, submitted_at, stage FROM submissions
                    WHERE group_code=:g ORDER BY submitted_at DESC""", g=group)
    st.dataframe(sdf, use_container_width=True)

def page_galeria_avaliacao(auth):
    if not auth or auth.get("who")!="docente":
        st.warning("Acesso restrito a docentes.")
        return
    st.subheader("Avaliar trabalhos (em avaliação)")
    sdf = get_df("""SELECT id, group_code, theme_title, submitted_at, stage
                    FROM submissions WHERE stage='avaliacao' ORDER BY submitted_at DESC""")
    st.dataframe(sdf, use_container_width=True)
    sid = st.selectbox("Trabalho (ID)", sdf["id"].tolist() if not sdf.empty else [])
    like = st.toggle("Curtir")
    score = st.slider("Nota", 0, 10, 8)
    comments = st.text_area("Comentários (opcional)")
    if st.button("Salvar avaliação"):
        exec_sql("""INSERT INTO reviews(submission_id,instructor_id,liked,score,comments,created_at)
                    VALUES(:i,:p,:l,:s,:c,:ts)
                    ON CONFLICT(submission_id, instructor_id) DO UPDATE
                    SET liked=:l, score=:s, comments=:c, created_at=:ts""",
                 i=int(sid), p=int(auth["id"]), l=int(like), s=float(score), c=comments,
                 ts=datetime.now().isoformat(timespec="seconds"))
        st.success("Avaliação registrada.")

    st.markdown("---")
    st.subheader("Publicar na galeria (após avaliação)")
    pub_ids = st.multiselect("IDs para publicar", sdf["id"].tolist())
    if st.button("Publicar selecionados"):
        for i in pub_ids:
            exec_sql("UPDATE submissions SET stage='publicado' WHERE id=:i", i=int(i))
        st.success("Publicado(s) na galeria.")

    st.markdown("---")
    st.subheader("Métricas (publicados)")
    m = get_df("""SELECT s.id, s.group_code, s.theme_title,
                  ROUND(AVG(rv.score),2) AS media, SUM(rv.liked) AS likes
                  FROM submissions s LEFT JOIN reviews rv ON rv.submission_id=s.id
                  WHERE s.stage='publicado'
                  GROUP BY s.id ORDER BY likes DESC, media DESC""")
    st.dataframe(m, use_container_width=True)

def page_admin(auth):
    if not auth or auth.get("who")!="docente":
        st.warning("Acesso restrito a docentes.")
        return
    st.subheader("Temas (importar/atualizar)")
    tdf = get_df("SELECT number, title, category, status, reserved_by, reserved_at FROM themes ORDER BY number")
    st.dataframe(tdf, use_container_width=True, height=240)
    up_themes = st.file_uploader("Importar JSON de temas", type=["json"], key="themes_up")
    if up_themes and st.button("Carregar temas"):
        tmp = os.path.join(DATA_DIR, "_themes_upload.json")
        with open(tmp, "wb") as f: f.write(up_themes.read())
        addn = ensure_themes_from_json(tmp)
        st.success(f"Temas adicionados: {addn} (títulos repetidos são ignorados).")

    st.markdown("---")
    st.subheader("Parâmetros do semestre")
    term = st.text_input("Term (semestre letivo)", value=APP_TERM_DEFAULT)
    theme_lock = st.date_input("Data-limite para reservar tema com ≥5 membros",
                               value=date.fromisoformat(cfg_get("theme_lock_date", DEFAULT_THEME_LOCK_DATE)))
    if st.button("Salvar parâmetros"):
        cfg_set("theme_lock_date", theme_lock.isoformat())
        st.success("Parâmetros salvos.")

    st.markdown("---")
    st.subheader("Alunos (CSV) — colunas: ra,name,email,turma")
    up_alunos = st.file_uploader("CSV de alunos", type=["csv"])
    if up_alunos and st.button("Processar CSV"):
        df = pd.read_csv(up_alunos)
        with engine.begin() as conn:
            for row in df.to_dict(orient="records"):
                conn.execute(text("""INSERT OR IGNORE INTO students(ra,name,email,turma)
                                     VALUES(:ra,:name,:email,:turma)"""), row)
        st.success(f"{len(df)} aluno(s) processados.")

    st.markdown("---")
    st.subheader("Docentes")
    pdf = get_df("SELECT id,name,email,role,approved FROM professors ORDER BY role DESC, name")
    st.dataframe(pdf, use_container_width=True, height=220)
    name = st.text_input("Nome")
    email = st.text_input("E-mail")
    role = st.selectbox("Papel", [ROLE_DOCENTE, ROLE_ADMIN])
    pinp = st.text_input("PIN", type="password")
    approved = st.checkbox("Aprovado", value=True)
    if st.button("Salvar docente"):
        exec_sql("""INSERT INTO professors(name,email,role,pin,approved)
                    VALUES(:n,:e,:r,:p,:a)
                    ON CONFLICT(email) DO UPDATE SET name=:n, role=:r, pin=:p, approved=:a""",
                 n=name,e=email,r=role,p=pinp,a=1 if approved else 0)
        st.success("Docente salvo/atualizado.")

def page_galeria_publica():
    st.subheader("Galeria (publicados)")
    sdf = get_df("""SELECT id, group_code, theme_title, report_path, slides_path, zip_path,
                           video_path, audio_path, video_link, submitted_by, submitted_at
                    FROM submissions WHERE stage='publicado' ORDER BY submitted_at DESC""")
    st.dataframe(sdf, use_container_width=True)

# -----------------------------
# Roteamento
# -----------------------------
auth = st.session_state.get("auth")
if not auth:
    login_ui()
else:
    st.sidebar.write(f"**Logado:** {auth.get('name','')} ({auth.get('who')})")
    if st.sidebar.button("Sair"):
        logout()

    # abas visíveis por perfil
    if auth["who"]=="aluno":
        tabs = st.tabs(["Grupos & Temas", "Upload", "Galeria pública"])
        with tabs[0]: page_grupos_temas(auth)
        with tabs[1]: page_upload(auth)
        with tabs[2]: page_galeria_publica()
    else:
        tabs = st.tabs(["Grupos & Temas", "Upload (p/ depurar)", "Galeria/Avaliação (docente)", "Admin", "Galeria pública"])
        with tabs[0]: page_grupos_temas(auth)
        with tabs[1]: page_upload(auth)
        with tabs[2]: page_galeria_avaliacao(auth)
        with tabs[3]: page_admin(auth)
        with tabs[4]: page_galeria_publica()

st.caption("MVP – Submissões Industrial & EBC II (2º/2025) • Upload com vídeo/áudio e espelho opcional no SharePoint/OneDrive")
