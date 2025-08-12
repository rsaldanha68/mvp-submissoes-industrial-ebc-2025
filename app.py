# -*- coding: utf-8 -*-
import os, json, pathlib, re
from datetime import datetime
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

# -----------------------------------------------------------------------------
# CONFIG BÁSICA
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

# Pastas
DATA_DIR   = "data"
UPLOAD_DIR = "uploads"
PUBLIC_DIR = "public"
for p in (DATA_DIR, UPLOAD_DIR, PUBLIC_DIR, "app", "app/modules"):
    os.makedirs(p, exist_ok=True)

# Banco (SQLite)
DB_URL = f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}"
engine = create_engine(DB_URL, future=True)

# Parâmetros operacionais (podem ir em st.secrets)
TERM_ATUAL = st.secrets.get("TERM_ATUAL", "2025/2")
# Até esta data (inclusive) exigir 5+ alunos para reservar tema.
DEADLINE_MIN5 = st.secrets.get("DEADLINE_MIN5", "2025-10-15")
# Depois da data limite, permitir a partir de:
MIN_AFTER_DEADLINE = int(st.secrets.get("MIN_AFTER_DEADLINE", 3))

# -----------------------------------------------------------------------------
# BOOTSTRAP DO BANCO
# -----------------------------------------------------------------------------
def bootstrap_db():
    with engine.begin() as conn:
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS groups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            turma TEXT,                      -- MA6, MB6, NA6, NB6...
            course_code TEXT DEFAULT 'IND',  -- IND | EBCII | JOINT
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
          code TEXT UNIQUE, name TEXT NOT NULL
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
        CREATE TABLE IF NOT EXISTS pending_users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT,          -- 'aluno' | 'docente'
          email TEXT,
          created_at TEXT
        );""")

        # seeds
        conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('IND','Economia Industrial')"))
        conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('EBCII','Economia Brasileira II')"))
        conn.execute(text("INSERT OR IGNORE INTO semesters(term) VALUES(:t)"), {"t": TERM_ATUAL})

        # seed admin se não existir ninguém
        profs = conn.execute(text("SELECT COUNT(*) FROM professors")).scalar_one()
        if profs == 0:
            # usa secrets se houver, senão padrão
            admin_email = st.secrets.get("ADMIN_EMAIL", "rsaldanha@pucsp.br")
            admin_pin   = st.secrets.get("ADMIN_PIN", "admin")
            conn.execute(text("""
              INSERT INTO professors(name,email,role,pin)
              VALUES(:n,:e,'admin',:p)
              ON CONFLICT(email) DO NOTHING
            """), {"n":"Administrador", "e":admin_email, "p":admin_pin})

bootstrap_db()

# -----------------------------------------------------------------------------
# THEMES: carregar/atualizar do JSON (merge por título)
# -----------------------------------------------------------------------------
def ensure_themes_from_json(path_json: str) -> int:
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
                         {"n": int(it.get("number", 0) or 0),
                          "t": title,
                          "c": it.get("category", "Outro")})
            inserted += 1
    return inserted

_added = ensure_themes_from_json(os.path.join("data", "themes_2025_2.json"))
if _added:
    st.sidebar.success(f"Temas carregados: +{_added}")

# -----------------------------------------------------------------------------
# HELPERS BD
# -----------------------------------------------------------------------------
def get_df(sql: str, **params):
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params)

def exec_sql(sql: str, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)

def list_groups():
    return get_df("SELECT id, code, turma, course_code FROM groups ORDER BY turma, code")

def group_details(code: str):
    dfm = get_df("""SELECT gm.student_name FROM group_members gm
                    JOIN groups g ON gm.group_id=g.id WHERE g.code=:c""", c=code)
    return dfm["student_name"].tolist() if not dfm.empty else []

def list_free_themes(category: str | None = None):
    if category and category != "Todos":
        df = get_df("SELECT title FROM themes WHERE status='livre' AND category=:c ORDER BY number", c=category)
    else:
        df = get_df("SELECT title FROM themes WHERE status='livre' ORDER BY number")
    return df["title"].tolist()

def reserve_theme(theme_title: str, group_code: str, min_required: int):
    members = group_details(group_code)
    if len(members) < min_required:
        return False, f"Para reservar tema agora são necessários {min_required} membros no grupo."
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

def turma_majoritaria(nomes: list[str], turma_fallback: str | None) -> str:
    if not nomes and turma_fallback:
        return turma_fallback
    if not nomes:
        return "MA6"
    q = get_df("SELECT name, turma FROM students WHERE name IN :ns", ns=tuple(nomes))
    cont = q["turma"].value_counts().to_dict() if not q.empty else {}
    if not cont and turma_fallback:
        return turma_fallback
    if not cont:
        return "MA6"
    top = sorted(cont.items(), key=lambda kv: (-kv[1], kv[0]))
    return top[0][0] if isinstance(top[0], str) else top[0][0]

def proximo_codigo_por_turma(turma: str) -> str:
    df = get_df("SELECT code FROM groups WHERE turma=:t", t=turma)
    max_n = 0
    for c in df["code"].tolist():
        m = re.search(rf"^{re.escape(turma)}G(\d+)$", c or "", re.I)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{turma}G{max_n+1}"

# -----------------------------------------------------------------------------
# LOGIN / SESSÃO
# -----------------------------------------------------------------------------
def login_ui():
    st.header("Login")
    role = st.radio("Sou:", ["Aluno", "Docente"], index=1, horizontal=True)
    email = st.text_input("E-mail institucional")
    pin = st.text_input("PIN", type="password") if role == "Docente" else None

    if st.button("Entrar", type="primary"):
        if role == "Docente":
            dfp = get_df("SELECT * FROM professors WHERE email=:e AND pin=:p", e=email.strip(), p=(pin or "").strip())
            if dfp.empty:
                st.error("Credenciais inválidas. Tente novamente.")
            else:
                st.session_state["auth"] = {"role":"docente","email":email.strip(),
                                            "name": dfp.iloc[0]["name"], "prof_id": int(dfp.iloc[0]["id"]),
                                            "is_admin": (dfp.iloc[0]["role"] == "admin")}
                st.rerun()
        else:
            # aluno por e-mail
            dfa = get_df("SELECT * FROM students WHERE email=:e AND active=1", e=email.strip())
            if dfa.empty:
                st.error("E-mail não encontrado em 'students'. Vou notificar a coordenação.")
                exec_sql("INSERT INTO pending_users(kind,email,created_at) VALUES('aluno',:e,:ts)",
                         e=email.strip(), ts=datetime.now().isoformat(timespec="seconds"))
            else:
                st.session_state["auth"] = {"role":"aluno","email":email.strip(),
                                            "name": dfa.iloc[0]["name"], "student_id": int(dfa.iloc[0]["id"]),
                                            "turma": dfa.iloc[0]["turma"]}
                st.rerun()

if "auth" not in st.session_state:
    login_ui()
    st.stop()

auth = st.session_state["auth"]
st.success(f"Bem-vindo, {auth.get('name','usuário')}!")

# Tabs por papel
if auth["role"] == "aluno":
    tabs = st.tabs(["1) Grupos & Temas", "2) Upload"])
else:
    tabs = st.tabs(["1) Grupos & Temas", "2) Upload", "3) Galeria/Avaliação", "4) Administração", "5) Alunos & Docentes"])

# -----------------------------------------------------------------------------
# 1) GRUPOS & TEMAS
# -----------------------------------------------------------------------------
with tabs[0]:
    st.subheader("Grupos (5–6 recomendado)")
    gdf_all = list_groups()

    # Criar/entrar grupo
    c1, c2 = st.columns(2)
    with c1:
        turma_pref = st.selectbox("Turma base (p/ desempate)", ["MA6","MB6","NA6","NB6"], index=0, key="turma_pref")
    with c2:
        course = st.selectbox("Disciplina do grupo", ["IND","EBCII","JOINT"], index=0, key="course_code")

    # Sugerir código automático a partir do próprio aluno (iniciador)
    nomes_tmp = [auth.get("name","")]
    turma_escolhida = turma_majoritaria(nomes_tmp, turma_pref)
    code_sugerido = proximo_codigo_por_turma(turma_escolhida)

    st.info(f"Sugestão de código (pela turma de maioria dos membros): **{code_sugerido}**")
    if st.button("Criar meu grupo com esse código"):
        try:
            exec_sql("""INSERT INTO groups(code,turma,course_code,created_by,created_at)
                        VALUES(:c,:t,:cc,:u,:ts)""",
                     c=code_sugerido, t=turma_escolhida, cc=course,
                     u=auth.get("name",""), ts=datetime.now().isoformat(timespec="seconds"))
            # adiciona auto ao grupo
            gid = int(get_df("SELECT id FROM groups WHERE code=:c", c=code_sugerido)["id"].iloc[0])
            exec_sql("INSERT OR IGNORE INTO group_members(group_id,student_name) VALUES(:g,:n)",
                     g=gid, n=auth.get("name",""))
            st.success(f"Grupo {code_sugerido} criado e você foi adicionado.")
        except Exception as e:
            st.error(f"Erro ao criar: {e}")

    st.markdown("---")
    st.subheader("Adicionar membros (busca por turma + autocomplete)")

    gdf = list_groups()
    if gdf.empty:
        st.info("Crie um grupo primeiro.")
    else:
        meus_grupos = gdf[gdf["code"].str.contains(turma_escolhida[:2], na=False)]["code"].tolist() or gdf["code"].tolist()
        sel_group = st.selectbox("Selecione o grupo", sorted(set(meus_grupos + gdf["code"].tolist())), key="sel_group_add")

        # filtro por turma e busca
        turmas_disp = ["Todas","MA6","MB6","NA6","NB6"]
        turma_filtro = st.selectbox("Filtrar turma", turmas_disp, index=turmas_disp.index(auth.get("turma","MA6")) if auth["role"]=="aluno" else 0)
        if turma_filtro == "Todas":
            cand = get_df("SELECT name FROM students WHERE active=1 ORDER BY name")
        else:
            cand = get_df("SELECT name FROM students WHERE turma=:t AND active=1 ORDER BY name", t=turma_filtro)
        nomes = cand["name"].tolist()
        novo_membro = st.selectbox("Adicionar membro (digite para filtrar)", nomes, index=None, placeholder="Nome do aluno…", key="membro_auto")

        colx, coly = st.columns(2)
        if colx.button("Adicionar ao grupo"):
            if not novo_membro:
                st.error("Selecione um nome.")
            else:
                gid = int(gdf[gdf["code"] == sel_group]["id"].iloc[0])
                try:
                    exec_sql("INSERT OR IGNORE INTO group_members(group_id,student_name) VALUES(:g,:n)",
                             g=gid, n=novo_membro)
                    st.success("Membro adicionado.")
                except Exception as e:
                    st.error(f"Erro ao adicionar: {e}")

        if coly.button("Remover último membro"):
            gid = int(gdf[gdf["code"] == sel_group]["id"].iloc[0])
            exec_sql("""DELETE FROM group_members
                        WHERE rowid IN (SELECT rowid FROM group_members
                                        WHERE group_id=:g ORDER BY rowid DESC LIMIT 1)""", g=gid)
            st.warning("Último membro removido.")

        st.write("Membros atuais:", group_details(sel_group))

    st.markdown("---")
    st.subheader("Reserva de tema (com mínimo por data)")

    if gdf.empty:
        st.info("Crie um grupo e adicione membros antes de reservar tema.")
    else:
        hoje = datetime.now().date()
        limite = datetime.fromisoformat(DEADLINE_MIN5).date()
        min_req = 5 if hoje <= limite else MIN_AFTER_DEADLINE
        st.caption(f"Regra atual: mínimo de **{min_req}** membros para reservar.")

        sel_group2 = st.selectbox("Grupo para reservar", gdf["code"].tolist(), key="reserve_group")
        cat_res = st.selectbox("Filtrar por categoria", ["Todos","Privatização","Concessão","PPP","Financiamento/BNDES","Outro"], key="cat_res")
        free_list = list_free_themes(cat_res)
        theme_choice = st.selectbox("Temas disponíveis", free_list, index=0 if free_list else None)

        cols = st.columns(2)
        if cols[0].button("Reservar tema"):
            if not theme_choice:
                st.error("Escolha um tema.")
            else:
                ok, msg = reserve_theme(theme_choice, sel_group2, min_req)
                st.success(msg) if ok else st.error(msg)

        my_reserved = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=sel_group2)["title"].tolist()
        release_sel = st.selectbox("Liberar tema reservado (do seu grupo)", my_reserved, key="rel_sel") if my_reserved else None
        if cols[1].button("Liberar tema"):
            if not release_sel:
                st.error("Seu grupo não possui tema reservado.")
            else:
                ok, msg = release_theme(release_sel, auth.get("name",""))
                st.warning(msg) if ok else st.error(msg)

    st.markdown("---")
    st.subheader("Status dos temas")
    cat_view = st.selectbox("Categoria", ["Todos","Privatização","Concessão","PPP","Financiamento/BNDES","Outro"], key="cat_view")
    if cat_view == "Todos":
        tdf = get_df("SELECT number, title, category, status, reserved_by, reserved_at FROM themes ORDER BY status DESC, number")
    else:
        tdf = get_df("""SELECT number, title, category, status, reserved_by, reserved_at
                        FROM themes WHERE category=:c ORDER BY status DESC, number""", c=cat_view)
    st.dataframe(tdf, use_container_width=True)

# -----------------------------------------------------------------------------
# 2) UPLOAD
# -----------------------------------------------------------------------------
with tabs[1]:
    st.subheader("Upload de trabalhos finais")
    gdf = list_groups()
    if gdf.empty:
        st.info("Crie um grupo primeiro.")
    else:
        # Se aluno, prioriza grupo em que ele é membro; se prof, lista todos
        if auth["role"] == "aluno":
            meus = []
            for _, r in gdf.iterrows():
                if auth.get("name","") in group_details(r["code"]):
                    meus.append(r["code"])
            opts = meus or gdf["code"].tolist()
        else:
            opts = gdf["code"].tolist()

        group = st.selectbox("Grupo", opts)
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
                                VALUES(:g,:t,:r,:s,:z,:v,:c,:u,:ts,0)""",
                             g=group, t=theme, r=rpath, s=spath, z=zpath, v=video, c=1 if consent else 0,
                             u=auth.get("name",""), ts=datetime.now().isoformat(timespec="seconds"))
                    st.success("Submissão recebida.")

    st.markdown("---")
    st.subheader("Submissões do seu grupo")
    if gdf.empty:
        pass
    else:
        if auth["role"] == "aluno":
            vopts = []
            for _, r in gdf.iterrows():
                if auth.get("name","") in group_details(r["code"]):
                    vopts.append(r["code"])
            vopts = vopts or gdf["code"].tolist()
        else:
            vopts = gdf["code"].tolist()
        group2 = st.selectbox("Ver submissões do grupo", vopts, key="sub_view")
        sdf = get_df("""SELECT id, theme_title, report_path, slides_path, zip_path, video_link,
                       submitted_by, submitted_at, approved FROM submissions
                       WHERE group_code=:g ORDER BY submitted_at DESC""", g=group2)
        st.dataframe(sdf, use_container_width=True)

# -----------------------------------------------------------------------------
# 3) GALERIA / AVALIAÇÃO (somente docentes)
# -----------------------------------------------------------------------------
if auth["role"] == "docente" and len(tabs) >= 3:
    with tabs[2]:
        st.subheader("Avaliação por docentes")
        pr = get_df("SELECT id,name FROM professors WHERE email=:e", e=auth["email"])
        instr_id = pr["id"].iloc[0]
        sdf = get_df("""SELECT id, group_code, theme_title, submitted_at
                        FROM submissions WHERE approved=1 ORDER BY submitted_at DESC""")
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

# -----------------------------------------------------------------------------
# 4) ADMINISTRAÇÃO (somente docentes)
# -----------------------------------------------------------------------------
if auth["role"] == "docente" and len(tabs) >= 4:
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
        st.subheader("Temas (importar/atualizar, liberar/travar)")
        tdf = get_df("SELECT number, title, category, status, reserved_by, reserved_at FROM themes ORDER BY number")
        st.dataframe(tdf, use_container_width=True)
        up_themes = st.file_uploader("Importar JSON de temas", type=["json"], key="themes_up")
        if up_themes and st.button("Carregar temas"):
            tmp = os.path.join(DATA_DIR, "_themes_upload.json")
            with open(tmp, "wb") as f:
                f.write(up_themes.read())
            addn = ensure_themes_from_json(tmp)
            st.success(f"Temas adicionados: {addn}. (títulos existentes são ignorados)")

# -----------------------------------------------------------------------------
# 5) ALUNOS & DOCENTES (somente docentes)
# -----------------------------------------------------------------------------
if auth["role"] == "docente" and len(tabs) >= 5:
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
        term = st.text_input("Semestre (term)", value=TERM_ATUAL)
        up_txt = st.file_uploader("Arquivos .txt", type=["txt"], accept_multiple_files=True, key="txts")
        if up_txt and st.button("Processar TXT"):
            try:
                from app.modules.import_txt import parse_puc_txt, upsert_students_and_enroll  # caminho esperado
                ok_count = 0
                temp_dir = pathlib.Path(DATA_DIR) / "_tmp"; temp_dir.mkdir(parents=True, exist_ok=True)
                disc_map = {"ECONOMIA INDUSTRIAL":"IND", "EBC II":"EBCII", "EBCII":"EBCII"}
                for upl in up_txt:
                    fp = temp_dir / upl.name
                    fp.write_bytes(upl.read())
                    meta = parse_puc_txt(str(fp))
                    turma_txt = (meta.get("turma") or "")
                    disc_code = disc_map.get(meta.get("disciplina","").upper(), "IND")
                    if not turma_txt:
                        st.warning(f"{upl.name}: turma não detectada; ignorado.")
                        continue
                    upsert_students_and_enroll(engine, term, disc_code, turma_txt, meta.get("students", []))
                    ok_count += 1
                st.success(f"TXT processados: {ok_count}")
            except Exception as e:
                st.error("Módulo 'app/modules/import_txt.py' não encontrado ou com erro.")
                st.info("Crie 'app/modules/import_txt.py' com as funções parse_puc_txt(...) e upsert_students_and_enroll(...).")

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
        st.subheader("Docentes")
        name = st.text_input("Nome")
        email = st.text_input("E-mail")
        role = st.selectbox("Papel", ["docente","admin"])
        pinp = st.text_input("PIN", type="password")
        if st.button("Salvar docente"):
            exec_sql("""INSERT INTO professors(name,email,role,pin) VALUES(:n,:e,:r,:p)
                        ON CONFLICT(email) DO UPDATE SET name=:n, role=:r, pin=:p""",
                     n=name,e=email,r=role,p=pinp)
            st.success("Docente salvo/atualizado.")

st.caption("MVP – Submissões Industrial & EBC II (2º/2025)")
