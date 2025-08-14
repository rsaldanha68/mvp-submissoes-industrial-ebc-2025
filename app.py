import os, json, pathlib, re
from datetime import datetime, date
from typing import Optional, Tuple

# Desativa watcher para evitar "inotify instance limit reached" em cloud
os.environ.setdefault("STREAMLIT_SERVER_FILE_WATCHER_TYPE", "none")

import streamlit as st
import pandas as pd
import requests
from sqlalchemy import create_engine, text

st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

# ========= 0) Segredos / Variáveis =========
SECRETS = dict(getattr(st, "secrets", {}))
ENV = os.environ

ADMIN_EMAIL = (SECRETS.get("ADMIN_EMAIL") or ENV.get("ADMIN_EMAIL") or "").strip()
ADMIN_PIN   = (SECRETS.get("ADMIN_PIN")   or ENV.get("ADMIN_PIN")   or "admin").strip()

TENANT_ID     = SECRETS.get("TENANT_ID")     or ENV.get("TENANT_ID")
CLIENT_ID     = SECRETS.get("CLIENT_ID")     or ENV.get("CLIENT_ID")
CLIENT_SECRET = SECRETS.get("CLIENT_SECRET") or ENV.get("CLIENT_SECRET")
SP_SITE_URL   = SECRETS.get("SP_SITE_URL")   or ENV.get("SP_SITE_URL")
SP_DRIVE_NAME = SECRETS.get("SP_DRIVE_NAME") or ENV.get("SP_DRIVE_NAME")
SP_BASE_FOLDER= SECRETS.get("SP_BASE_FOLDER")or ENV.get("SP_BASE_FOLDER")

# ========= 1) Pastas / Banco =========
DATA_DIR, UPLOAD_DIR, PUBLIC_DIR = "data", "uploads", "public"
for p in (DATA_DIR, UPLOAD_DIR, PUBLIC_DIR):
    os.makedirs(p, exist_ok=True)

DB_URL = f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}"
engine = create_engine(DB_URL, future=True)

# ========= 2) Schema =========
def bootstrap_db():
    with engine.begin() as conn:
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS settings(
          key TEXT PRIMARY KEY,
          value TEXT
        )""")
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS groups(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT UNIQUE,
          turma TEXT,
          created_by TEXT,
          created_at TEXT
        )""")
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS group_members(
          group_id INTEGER NOT NULL,
          student_name TEXT NOT NULL,
          turma TEXT,
          UNIQUE(group_id, student_name)
        )""")
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS themes(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          number INTEGER,
          title TEXT UNIQUE,
          category TEXT,
          status TEXT CHECK (status IN ('livre','reservado')) DEFAULT 'livre',
          reserved_by TEXT,
          reserved_at TEXT
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
          approved INTEGER DEFAULT 0,
          sp_urls TEXT
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
          role TEXT CHECK (role IN ('admin','docente')) DEFAULT 'docente',
          pin TEXT,
          approved INTEGER DEFAULT 1
        )""")
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS reviews(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          submission_id INTEGER NOT NULL,
          instructor_id INTEGER NOT NULL,
          score REAL,
          liked INTEGER DEFAULT 0,
          comment TEXT,
          created_at TEXT,
          UNIQUE(submission_id, instructor_id)
        )""")
        # Defaults de política
        conn.execute(text("""
        INSERT OR IGNORE INTO settings(key,value) VALUES
        ('MIN_GROUP_SIZE','5'),
        ('ENFORCE_UNTIL','2025-09-01'),
        ('SP_ENABLED','1')
        """))

bootstrap_db()

# ========= 3) Utils BD =========
def get_df(sql: str, **params):
    with engine.begin() as c:
        return pd.read_sql(text(sql), c, params=params)

def exec_sql(sql: str, **params):
    with engine.begin() as c:
        c.execute(text(sql), params)

def get_setting(key: str, default: str = ""):
    df = get_df("SELECT value FROM settings WHERE key=:k", k=key)
    return (df["value"].iloc[0] if not df.empty else default) or default

def set_setting(key: str, value: str):
    exec_sql("""
      INSERT INTO settings(key,value) VALUES(:k,:v)
      ON CONFLICT(key) DO UPDATE SET value=:v
    """, k=key, v=str(value))

# ========= 4) Temas (seed opcional) =========
def ensure_themes_from_json(path_json: str) -> int:
    if not os.path.exists(path_json):
        return 0
    items = json.loads(pathlib.Path(path_json).read_text("utf-8")) or []
    inserted = 0
    with engine.begin() as conn:
        existing = pd.read_sql("SELECT title FROM themes", conn)
        have = set(existing["title"].tolist()) if not existing.empty else set()
        for it in items:
            title = (it.get("title") or "").strip()
            if not title or title in have:
                continue
            num = int(it.get("number") or 0)
            cat = (it.get("category") or "Outro").strip()
            conn.execute(text("""
              INSERT INTO themes(number,title,category,status)
              VALUES(:n,:t,:c,'livre')
            """), {"n": num, "t": title, "c": cat})
            inserted += 1
    return inserted

added = ensure_themes_from_json(os.path.join("data","themes_2025_2.json"))
if added:
    st.sidebar.success(f"Temas adicionados: +{added}")

# ========= 5) Professores (seed) =========
DOCENTES_SEED = [
    ("ROLAND VERAS SALDANHA JUNIOR", "rsaldanha@pucsp.br", "admin", "8722", 1),
    ("MARCIA FLAIRE PEDROZA",        "marciapedroza@pucsp.br", "docente", "1234", 1),
    ("JULIO MANUEL PIRES",           "jmpires@pucsp.br",       "docente", "1234", 1),
    ("Raphael Almeida Videira",      "ravideira@pucsp.br",     "docente", "1234", 1),
    ("Tomas Bruginski de Paula",     "tbruginski@pucsp.br",    "docente", "1234", 1),
]
with engine.begin() as conn:
    for n,e,r,p,ap in DOCENTES_SEED:
        conn.execute(text("""
          INSERT OR IGNORE INTO professors(name,email,role,pin,approved)
          VALUES(:n,:e,:r,:p,:a)
        """), {"n":n, "e":e.lower(), "r":r, "p":p, "a":ap})

# ========= 6) Grupos & Regras =========
def list_groups():
    return get_df("SELECT id, code, turma FROM groups ORDER BY turma, code")

def group_members(code: str):
    return get_df("""
      SELECT gm.student_name, gm.turma
        FROM group_members gm
        JOIN groups g ON g.id=gm.group_id
       WHERE g.code=:c
    """, c=code)

def next_group_code_for_turma(turma: str) -> str:
    base = turma.upper().strip()
    df = get_df("SELECT code FROM groups WHERE turma=:t ORDER BY code", t=base)
    nums = []
    for c in df["code"].tolist():
        m = re.search(r"G(\d+)$", c)
        if m: nums.append(int(m.group(1)))
    nxt = (max(nums)+1) if nums else 1
    return f"{base}G{nxt}"

def enforce_min_group() -> Tuple[int, bool]:
    from dateutil.parser import isoparse
    min_sz = int(get_setting("MIN_GROUP_SIZE","5"))
    until = get_setting("ENFORCE_UNTIL","2025-09-01")
    try:
        enforce = date.today() <= isoparse(until).date()
    except Exception:
        enforce = True
    return min_sz, enforce

def reserve_theme(theme_title: str, group_code: str) -> Tuple[bool,str]:
    min_sz, enforce = enforce_min_group()
    mdf = group_members(group_code)
    if enforce and (len(mdf) < min_sz):
        return False, f"Grupo abaixo de {min_sz} integrante(s) até a data-limite."
    with engine.begin() as c:
        row = c.execute(text("SELECT status FROM themes WHERE title=:t"), {"t":theme_title}).fetchone()
        if not row or row[0] != "livre":
            return False, "Tema já reservado."
        c.execute(text("""
          UPDATE themes
             SET status='reservado', reserved_by=:g, reserved_at=:ts
           WHERE title=:t
        """), {"g":group_code, "t":theme_title, "ts":datetime.now().isoformat(timespec="seconds")})
    return True, "Tema reservado."

def release_theme(theme_title: str) -> Tuple[bool,str]:
    with engine.begin() as c:
        row = c.execute(text("SELECT status FROM themes WHERE title=:t"), {"t":theme_title}).fetchone()
        if not row or row[0] != "reservado":
            return False, "Tema não está reservado."
        c.execute(text("""
          UPDATE themes
             SET status='livre', reserved_by=NULL, reserved_at=NULL
           WHERE title=:t
        """), {"t":theme_title})
    return True, "Tema liberado."

# ========= 7) SharePoint (Graph) =========
def graph_token() -> Optional[str]:
    if not (TENANT_ID and CLIENT_ID and CLIENT_SECRET):
        return None
    try:
        resp = requests.post(
            f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
            data={
                "grant_type":"client_credentials",
                "client_id":CLIENT_ID,
                "client_secret":CLIENT_SECRET,
                "scope":"https://graph.microsoft.com/.default"
            },
            timeout=30
        )
        if resp.ok:
            return resp.json().get("access_token")
    except Exception:
        return None
    return None

@st.cache_resource(show_spinner=False)
def _resolve_site_drive(_site_url: str, _drive_name: str):
    token = graph_token()
    if not token:
        return None, None
    host = re.sub(r"^https://", "", _site_url).split("/")[0]
    rel  = _site_url.replace(f"https://{host}", "")
    s = requests.get(
        f"https://graph.microsoft.com/v1.0/sites/{host}:{rel}",
        headers={"Authorization":f"Bearer {token}"},
        timeout=30
    )
    if not s.ok:
        return None, None
    site_id = s.json().get("id")
    d = requests.get(
        f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives",
        headers={"Authorization":f"Bearer {token}"},
        timeout=30
    )
    if not d.ok:
        return site_id, None
    drive_id = None
    for drv in d.json().get("value", []):
        if drv.get("name") == _drive_name:
            drive_id = drv.get("id"); break
    return site_id, drive_id

def sp_upload_bytes(folder: str, filename: str, data: bytes) -> Optional[str]:
    if get_setting("SP_ENABLED","1") != "1":
        return None
    if not (SP_SITE_URL and SP_DRIVE_NAME):
        return None
    token = graph_token()
    if not token:
        return None
    site_id, drive_id = _resolve_site_drive(SP_SITE_URL, SP_DRIVE_NAME)
    if not (site_id and drive_id):
        return None

    clean_folder = "/".join([x for x in [SP_BASE_FOLDER or "", folder or ""] if x]).strip("/")
    target_path  = f"{clean_folder}/{filename}" if clean_folder else filename
    url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{target_path}:/content"
    r = requests.put(url, headers={"Authorization":f"Bearer {token}"}, data=data, timeout=120)
    if r.ok:
        return r.json().get("webUrl")
    return None

# ========= 8) Login =========
def login_block():
    st.subheader("Login")
    who = st.radio("Sou:", ["Aluno","Docente"], horizontal=True)

    if who == "Docente":
        email = st.text_input("E-mail institucional", key="doc_email")
        pin   = st.text_input("PIN", type="password", key="doc_pin")
        if st.button("Entrar (docente)", key="doc_login_btn"):
            email_norm = (email or "").strip().lower()
            with engine.begin() as conn:
                prof = conn.execute(text("""
                    SELECT id, name, email, role, pin, approved
                      FROM professors
                     WHERE LOWER(email)=:e
                """), {"e":email_norm}).mappings().fetchone()  # <- evita tuple error

                # Auto-provisiona admin pelo ADMIN_EMAIL
                if not prof and ADMIN_EMAIL and email_norm == ADMIN_EMAIL.lower():
                    conn.execute(text("""
                        INSERT INTO professors(name,email,role,pin,approved)
                        VALUES('Administrador', :e, 'admin', :p, 1)
                    """), {"e": email_norm, "p": (pin or ADMIN_PIN)})
                    prof = conn.execute(text("""
                        SELECT id, name, email, role, pin, approved
                          FROM professors WHERE LOWER(email)=:e
                    """), {"e":email_norm}).mappings().fetchone()

                if not prof:
                    st.error("Conta de docente não encontrada. Solicite acesso na aba Administração.")
                elif int(prof.get("approved", 0)) != 1:
                    st.warning("Conta pendente de aprovação.")
                elif (pin or "") != (prof.get("pin") or ""):
                    st.error("PIN inválido.")
                else:
                    st.session_state["auth"] = {
                        "who":"docente",
                        "id":int(prof["id"]),
                        "email":prof["email"],
                        "name":prof["name"],
                        "role":prof.get("role") or "docente"
                    }
                    st.success("Login efetuado.")
                    st.experimental_rerun()

    else:
        ra = st.text_input("RA", key="al_ra")
        if st.button("Entrar (aluno)", key="al_login_btn"):
            df = get_df("SELECT id,ra,name,email,turma FROM students WHERE ra=:r AND active=1", r=(ra or "").strip())
            if df.empty:
                st.error("RA não encontrado. Solicite cadastro na aba Alunos & Docentes.")
            else:
                row = df.iloc[0]
                st.session_state["auth"] = {
                    "who":"aluno",
                    "id":int(row["id"]),
                    "ra":row["ra"],
                    "name":row["name"],
                    "turma":row["turma"],
                    "email":row["email"]
                }
                st.success(f"Bem-vindo(a), {row['name']}!")
                st.experimental_rerun()

def can_access(tab_for:str)->bool:
    a = st.session_state.get("auth")
    if not a: return False
    if tab_for == "aluno":  return a.get("who") == "aluno"
    if tab_for == "docente":return a.get("who") == "docente"
    if tab_for == "admin":  return a.get("who") == "docente" and a.get("role") == "admin"
    return False

# ========= 9) UI =========
st.title("Submissões – Industrial & EBC II (2º/2025)")
auth = st.session_state.get("auth")
if not auth:
    login_block()
    st.stop()

tabs = st.tabs([
    "1) Grupos & Temas",
    "2) Upload",
    "3) Pré-Galeria (avaliação)",
    "4) Administração",
    "5) Alunos & Docentes"
])

# ----- 1) Grupos & Temas
with tabs[0]:
    st.subheader("Gerenciar grupos e temas")
    from dateutil.parser import isoparse
    min_sz, enforce = enforce_min_group()
    st.caption(f"Regra: mínimo {min_sz} integrante(s){' (até ' + get_setting('ENFORCE_UNTIL') + ')' if enforce else ''}.")

    c1,c2,c3 = st.columns([1,1,2])
    with c1:
        turma = st.text_input("Turma", value=(auth.get("turma") if auth["who"]=="aluno" else "MA6")).upper()
    with c2:
        auto_code = st.checkbox("Código automático", value=True)
        code_input = st.text_input("Código (ex.: MA6G1)") if not auto_code else next_group_code_for_turma(turma) if turma else ""
    with c3:
        created_by = st.text_input("Seu nome", value=auth.get("name",""))

    if st.button("Criar grupo"):
        code = code_input if not auto_code else next_group_code_for_turma(turma)
        try:
            exec_sql("""INSERT INTO groups(code,turma,created_by,created_at)
                        VALUES(:c,:t,:u,:ts)""",
                     c=code, t=turma, u=created_by.strip(),
                     ts=datetime.now().isoformat(timespec="seconds"))
            st.success(f"Grupo {code} criado.")
        except Exception as e:
            st.error(f"Falha: {e}")

    st.markdown("---")
    st.subheader("Adicionar membros")
    gdf = list_groups()
    if gdf.empty:
        st.info("Nenhum grupo. Crie um primeiro.")
    else:
        sel_group = st.selectbox("Grupo", gdf["code"].tolist())
        members = group_members(sel_group)
        st.dataframe(members, use_container_width=True)

        # Busca por RA/nome
        sterm = st.text_input("Pesquisar aluno (RA/nome contém)")
        if sterm:
            q = f"%{sterm.lower()}%"
            sdf = get_df("""
              SELECT id,ra,name,turma
                FROM students
               WHERE active=1 AND (LOWER(ra) LIKE :q OR LOWER(name) LIKE :q)
               ORDER BY turma,name LIMIT 100
            """, q=q)
        else:
            sdf = pd.DataFrame()

        sel = st.selectbox(
            "Selecione",
            sdf.apply(lambda r: f"{r['ra']} — {r['name']} ({r['turma']})", axis=1).tolist()
            if not sdf.empty else [], index=None
        )
        if st.button("Adicionar ao grupo"):
            if not sel:
                st.warning("Escolha um aluno.")
            else:
                ra = sel.split(" — ")[0]
                row = get_df("SELECT name,turma FROM students WHERE ra=:r", r=ra).iloc[0]
                gid = int(gdf[gdf["code"] == sel_group]["id"].iloc[0])
                try:
                    exec_sql("""
                      INSERT INTO group_members(group_id,student_name,turma)
                      VALUES(:g,:n,:t)
                    """, g=gid, n=row["name"], t=row["turma"])
                    st.success("Adicionado.")
                except Exception as e:
                    st.error(str(e))

    st.markdown("---")
    st.subheader("Reserva de tema")
    if not gdf.empty:
        sel_group2 = st.selectbox("Grupo", gdf["code"].tolist(), key="res_g")
        cat = st.selectbox("Categoria", ["Todos","Privatização","Concessão","PPP","Financiamento/BNDES","Outro"])
        if cat == "Todos":
            free = get_df("SELECT title FROM themes WHERE status='livre' ORDER BY number")["title"].tolist()
        else:
            free = get_df("SELECT title FROM themes WHERE status='livre' AND category=:c ORDER BY number", c=cat)["title"].tolist()
        choice = st.selectbox("Temas disponíveis", free)
        colr1,colr2 = st.columns(2)
        if colr1.button("Reservar"):
            ok,msg = reserve_theme(choice, sel_group2)
            st.success(msg) if ok else st.error(msg)
        my_reserved = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=sel_group2)["title"].tolist()
        rel = st.selectbox("Liberar tema do grupo", my_reserved) if my_reserved else None
        if colr2.button("Liberar"):
            if not rel: st.warning("Nada reservado.")
            else:
                ok,msg = release_theme(rel)
                st.success(msg) if ok else st.error(msg)

    st.markdown("---")
    st.subheader("Status dos temas")
    cat2 = st.selectbox("Filtrar", ["Todos","Privatização","Concessão","PPP","Financiamento/BNDES","Outro"], key="viewcat")
    if cat2=="Todos":
        tdf = get_df("SELECT number,title,category,status,reserved_by,reserved_at FROM themes ORDER BY status DESC, number")
    else:
        tdf = get_df("SELECT number,title,category,status,reserved_by,reserved_at FROM themes WHERE category=:c ORDER BY status DESC, number", c=cat2)
    st.dataframe(tdf, use_container_width=True)

# ----- 2) Upload
with tabs[1]:
    st.subheader("Upload de trabalhos")
    gdf = list_groups()
    if gdf.empty:
        st.info("Crie grupo antes.")
    else:
        group = st.selectbox("Grupo", gdf["code"].tolist())
        tdf = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=group)
        theme = tdf["title"].iloc[0] if not tdf.empty else None
        if not theme:
            st.error("Grupo sem tema reservado.")
        else:
            st.write("Tema:", f"**{theme}**")
            report = st.file_uploader("Relatório (PDF)", type=["pdf"])
            slides = st.file_uploader("Apresentação (PPTX/PDF)", type=["pptx","pdf"])
            bundle = st.file_uploader("Arquivos adicionais (ZIP)", type=["zip"])
            video  = st.file_uploader("Vídeo (mp4/mov/webm) – opcional", type=["mp4","mov","webm"])
            audio  = st.file_uploader("Áudio (mp3/wav/m4a) – opcional", type=["mp3","wav","m4a"])
            video_link = st.text_input("Link de vídeo (YouTube/Stream/etc.) – opcional")
            consent = st.checkbox("Cedo os direitos patrimoniais à PUC-SP para divulgação acadêmica/extensionista, com crédito aos autores.")
            submitted_by = st.text_input("Seu nome (quem envia)", value=auth.get("name",""))

            if st.button("Enviar"):
                if not consent:
                    st.error("Marque a cessão de direitos.")
                else:
                    gdir = pathlib.Path(UPLOAD_DIR) / group.replace("/","_")
                    gdir.mkdir(parents=True, exist_ok=True)
                    sp_urls = {}

                    def save_and_push(up, fname):
                        if up is None: return None
                        path = gdir / fname
                        path.write_bytes(up.getbuffer())
                        url = sp_upload_bytes(group, fname, up.getbuffer())
                        if url: sp_urls[fname] = url
                        return str(path)

                    rpath = save_and_push(report, "relatorio.pdf")
                    spath = save_and_push(slides, "apresentacao."+ (slides.name.split(".")[-1] if slides else "pdf"))
                    zpath = save_and_push(bundle, "materiais.zip")
                    vpath = save_and_push(video,  "video."+ (video.name.split(".")[-1] if video else "mp4"))
                    apath = save_and_push(audio,  "audio."+ (audio.name.split(".")[-1] if audio else "mp3"))

                    exec_sql("""
                      INSERT INTO submissions(group_code,theme_title,report_path,slides_path,zip_path,video_path,audio_path,video_link,
                                              consent,submitted_by,submitted_at,approved,sp_urls)
                      VALUES(:g,:t,:r,:s,:z,:vp,:ap,:vl,:c,:u,:ts,0,:sp)
                    """, g=group, t=theme, r=rpath, s=spath, z=zpath, vp=vpath, ap=apath, vl=video_link,
                         c=1, u=submitted_by.strip(), ts=datetime.now().isoformat(timespec="seconds"),
                         sp=json.dumps(sp_urls, ensure_ascii=False))
                    st.success("Submissão recebida. Entrará na **Pré‑galeria** para avaliação dos docentes.")

    st.markdown("---")
    st.subheader("Minhas submissões")
    if not gdf.empty:
        gsel = st.selectbox("Ver submissões do grupo", gdf["code"].tolist(), key="sv")
        sdf = get_df("""
          SELECT id, theme_title, submitted_by, submitted_at, approved
            FROM submissions WHERE group_code=:g ORDER BY submitted_at DESC
        """, g=gsel)
        st.dataframe(sdf, use_container_width=True)

# ----- 3) Pré-Galeria (docente)
with tabs[2]:
    if not can_access("docente"):
        st.info("Acesso restrito a docentes.")
    else:
        st.subheader("Pré‑galeria (avaliação interna)")
        sdf = get_df("""
          SELECT id, group_code, theme_title, submitted_by, submitted_at, approved
            FROM submissions ORDER BY submitted_at DESC
        """)
        st.dataframe(sdf, use_container_width=True)
        sid = st.selectbox("Escolha o ID para avaliar", sdf["id"].tolist() if not sdf.empty else [])
        like = st.toggle("Curtir")
        score= st.slider("Nota", 0, 10, 8)
        comment = st.text_area("Comentário (opcional)")
        if st.button("Salvar avaliação"):
            exec_sql("""
              INSERT INTO reviews(submission_id,instructor_id,score,liked,comment,created_at)
              VALUES(:i,:p,:s,:l,:c,:ts)
              ON CONFLICT(submission_id,instructor_id) DO UPDATE
              SET score=:s, liked=:l, comment=:c, created_at=:ts
            """, i=int(sid), p=int(auth["id"]), s=float(score), l=int(like), c=comment,
                 ts=datetime.now().isoformat(timespec="seconds"))
            st.success("Avaliação registrada.")

        st.markdown("---")
        st.subheader("Métricas")
        m = get_df("""
          SELECT s.id, s.group_code, s.theme_title,
                 ROUND(AVG(rv.score),2) AS media,
                 SUM(rv.liked) AS likes,
                 COUNT(rv.id) AS votos
            FROM submissions s LEFT JOIN reviews rv ON rv.submission_id=s.id
           GROUP BY s.id ORDER BY likes DESC, media DESC
        """)
        st.dataframe(m, use_container_width=True)

# ----- 4) Administração (admin)
with tabs[3]:
    if not can_access("admin"):
        st.info("Acesso restrito à coordenação/admin.")
    else:
        st.subheader("Políticas")
        from dateutil.parser import isoparse
        min_g = st.number_input("Mínimo de integrantes", 1, 10, int(get_setting("MIN_GROUP_SIZE","5")))
        until = st.date_input("Aplicar mínimo até", value=isoparse(get_setting("ENFORCE_UNTIL","2025-09-01")).date())
        sp_en = st.checkbox("Replicar arquivos no SharePoint", value=get_setting("SP_ENABLED","1")=="1")
        if st.button("Salvar políticas"):
            set_setting("MIN_GROUP_SIZE", str(min_g))
            set_setting("ENFORCE_UNTIL", until.isoformat())
            set_setting("SP_ENABLED", "1" if sp_en else "0")
            st.success("Políticas atualizadas.")

        st.markdown("---")
        st.subheader("Publicar na galeria externa")
        sdf = get_df("SELECT id, group_code, theme_title, submitted_at, approved FROM submissions ORDER BY submitted_at DESC")
        st.dataframe(sdf, use_container_width=True)
        ids = st.multiselect("IDs para publicar (aprovar)", sdf["id"].tolist())
        if st.button("Publicar selecionados"):
            for i in ids:
                exec_sql("UPDATE submissions SET approved=1 WHERE id=:i", i=int(i))
            st.success("Publicado(s).")

        st.markdown("---")
        st.subheader("Gerir docentes")
        pdf = get_df("SELECT id,name,email,role,approved FROM professors ORDER BY role DESC, name")
        st.dataframe(pdf, use_container_width=True)
        c1,c2,c3,c4 = st.columns([2,2,1,1])
        with c1:
            pname = st.text_input("Nome")
        with c2:
            pemail= st.text_input("E-mail")
        with c3:
            prole = st.selectbox("Papel", ["docente","admin"])
        with c4:
            ppin  = st.text_input("PIN")
        if st.button("Salvar/Atualizar docente"):
            exec_sql("""
              INSERT INTO professors(name,email,role,pin,approved)
              VALUES(:n,:e,:r,:p,1)
              ON CONFLICT(email) DO UPDATE SET name=:n, role=:r, pin=:p, approved=1
            """, n=pname, e=pemail.lower().strip(), r=prole, p=ppin)
            st.success("Docente salvo.")
            st.experimental_rerun()

# ----- 5) Alunos & Docentes (docente)
with tabs[4]:
    if not can_access("docente"):
        st.info("Acesso restrito a docentes.")
    else:
        st.subheader("Importar alunos (CSV) — colunas: ra,name,email,turma")
        up = st.file_uploader("CSV", type=["csv"])
        if up and st.button("Processar CSV"):
            df = pd.read_csv(up)
            with engine.begin() as c:
                for rec in df.to_dict(orient="records"):
                    c.execute(text("""
                      INSERT OR IGNORE INTO students(ra,name,email,turma,active)
                      VALUES(:ra,:name,:email,:turma,1)
                    """), rec)
            st.success(f"{len(df)} aluno(s) processados.")

        st.markdown("---")
        st.subheader("Cadastro manual de aluno")
        ra  = st.text_input("RA")
        nm  = st.text_input("Nome")
        em  = st.text_input("E-mail")
        tm  = st.text_input("Turma", value="MA6")
        if st.button("Cadastrar aluno"):
            try:
                exec_sql("""
                  INSERT INTO students(ra,name,email,turma,active)
                  VALUES(:ra,:n,:e,:t,1)
                  ON CONFLICT(ra) DO UPDATE SET name=:n, email=:e, turma=:t, active=1
                """, ra=ra.strip(), n=nm.strip(), e=em.strip().lower(), t=tm.strip().upper())
                st.success("Aluno cadastrado/atualizado.")
            except Exception as e:
                st.error(str(e))

st.caption("MVP – Submissões Industrial & EBC II (2º/2025)")
