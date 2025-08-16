import os, io, json, re
from datetime import datetime
from typing import Optional, List, Dict

import streamlit as st
import pandas as pd
import requests
from sqlalchemy import create_engine, text

# ===================== Config inicial =====================
st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

DATA_DIR   = "data"
UPLOAD_DIR = "uploads"
PUBLIC_DIR = "public"
for p in (DATA_DIR, UPLOAD_DIR, PUBLIC_DIR):
    os.makedirs(p, exist_ok=True)

DB_URL = f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}"
engine = create_engine(DB_URL, future=True)

# Defaults (podem ser sobrescritos por secrets)
APP_TERM           = st.secrets.get("app", {}).get("TERM", "2025/2")
MIN_GROUP          = int(st.secrets.get("app", {}).get("MIN_GROUP", 4))
MAX_GROUP          = int(st.secrets.get("app", {}).get("MAX_GROUP", 5))
RESERVE_DEADLINE   = st.secrets.get("app", {}).get("RESERVE_DEADLINE", "2025-10-15T23:59:00")
PUBLISH_MIN_SCORE  = float(st.secrets.get("app", {}).get("PUBLISH_MIN_SCORE", 7.0))
MAX_GROUP_TOTAL_MB = int(st.secrets.get("app", {}).get("MAX_GROUP_TOTAL_MB", 400))

# Docentes predefinidos (nome, email, papel, PIN, aprovado, código da disciplina)
SEED_PROFESSORS = [
    ("ROLAND VERAS SALDANHA JUNIOR", "rsaldanha@pucsp.br", "admin", "8722", 1, "IND"),
    ("MARCIA FLAIRE PEDROZA",        "marciapedroza@pucsp.br", "docente", "", 1, "EBCII"),
    ("JULIO MANUEL PIRES",           "jmpires@pucsp.br",      "docente", "", 1, "EBCII"),
    ("RAPHAEL ALMEIDA VIDEIRA",      "ravideira@pucsp.br",    "docente", "", 1, "IND"),
    ("TOMAS BRUGINSKI DE PAULA",     "tbruginski@pucsp.br",   "docente", "", 1, "EBCII"),
]

# ===================== DB bootstrap/migrações =====================
def _add_col(conn, table, coldef):
    try:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {coldef}")
    except Exception:
        pass

with engine.begin() as conn:
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS students(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ra TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        email TEXT,
        turma TEXT,
        course_code TEXT,
        active INTEGER DEFAULT 1
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS professors(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        role TEXT,
        pin TEXT,
        discipline_code TEXT,
        approved INTEGER DEFAULT 0,
        created_at TEXT
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS disciplines(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        name TEXT NOT NULL
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS offerings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline_id INTEGER NOT NULL,
        term TEXT,
        class_name TEXT,
        PRIMARY KEY(discipline_id, term, class_name)
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS enrollments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        offering_id INTEGER NOT NULL,
        UNIQUE(student_id, offering_id)
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS groups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        turma TEXT,
        course_code TEXT DEFAULT 'JOINT',
        created_by TEXT,
        created_at TEXT
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS group_members(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        student_name TEXT NOT NULL
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS themes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number INTEGER,
        title TEXT UNIQUE NOT NULL,
        category TEXT,
        status TEXT CHECK (status IN ('livre','reservado')) DEFAULT 'livre',
        reserved_by TEXT,
        reserved_at TEXT,
        released_by TEXT,
        released_at TEXT
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS submissions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_code TEXT,
        theme_title TEXT,
        report_path TEXT,
        slides_path TEXT,
        zip_path TEXT,
        media_link TEXT,
        media_file_path TEXT,
        consent INTEGER DEFAULT 0,
        submitted_by TEXT,
        submitted_at TEXT,
        approved INTEGER DEFAULT 0
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS evaluations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL,
        instructor_id INTEGER NOT NULL,
        discipline_code TEXT NOT NULL,
        score_report REAL,
        score_slides REAL,
        score_media REAL,
        overall_score REAL,
        liked INTEGER DEFAULT 0,
        c_report TEXT,
        c_slides TEXT,
        c_media TEXT,
        c_overall TEXT,
        created_at TEXT,
        UNIQUE(submission_id, instructor_id, discipline_code)
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS config(
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    # Adiciona colunas novas caso não existam
    _add_col(conn, "professors", "approved INTEGER DEFAULT 0")
    _add_col(conn, "professors", "created_at TEXT")
    # Semeia valores padrão na tabela config
    def _set_default(k, v):
        conn.execute(text("INSERT OR IGNORE INTO config(key,value) VALUES(:k,:v)"), {"k": k, "v": v})
    _set_default("TERM", APP_TERM)
    _set_default("MIN_GROUP", str(MIN_GROUP))
    _set_default("MAX_GROUP", str(MAX_GROUP))
    _set_default("RESERVE_DEADLINE", RESERVE_DEADLINE)
    _set_default("PUBLISH_MIN_SCORE", str(PUBLISH_MIN_SCORE))
    _set_default("MAX_GROUP_TOTAL_MB", str(MAX_GROUP_TOTAL_MB))
    # Semeia contas de professores
    for name, email, role, pin, approved, disc in SEED_PROFESSORS:
        conn.execute(text("""
            INSERT OR IGNORE INTO professors(name,email,role,pin,approved,discipline_code,created_at)
            VALUES(:name, :email, :role, :pin, :approved, :disc, :created_at)
        """), {
            "name": name,
            "email": email,
            "role": role,
            "pin": pin,
            "approved": approved,
            "disc": disc,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    # Semeia disciplinas (IND, EBCII)
    conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('IND','Economia Industrial')"))
    conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('EBCII','Economia Brasileira II')"))

# Funções auxiliares de banco de dados
def get_df(sql: str, **params) -> pd.DataFrame:
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params)

def exec_sql(sql: str, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)

# Carrega valores de config do banco (pode ter sido atualizado)
TERM = get_df("SELECT value FROM config WHERE key='TERM'")["value"].iloc[0]
MIN_GROUP = int(get_df("SELECT value FROM config WHERE key='MIN_GROUP'")["value"].iloc[0])
MAX_GROUP = int(get_df("SELECT value FROM config WHERE key='MAX_GROUP'")["value"].iloc[0])
RESERVE_DEADLINE = get_df("SELECT value FROM config WHERE key='RESERVE_DEADLINE'")["value"].iloc[0]
PUBLISH_MIN_SCORE = float(get_df("SELECT value FROM config WHERE key='PUBLISH_MIN_SCORE'")["value"].iloc[0])
MAX_GROUP_TOTAL_MB = int(get_df("SELECT value FROM config WHERE key='MAX_GROUP_TOTAL_MB'")["value"].iloc[0])

# ===================== Carrega temas de arquivo JSON (se existir) =====================
themes_json_path = os.path.join(DATA_DIR, "themes_2025_2.json")
if os.path.exists(themes_json_path):
    df_themes = get_df("SELECT id FROM themes LIMIT 1")
    if df_themes.empty:
        try:
            with open(themes_json_path, "r", encoding="utf-8") as f:
                themes_list = json.load(f)
        except Exception:
            themes_list = []
        if themes_list:
            for i, it in enumerate(themes_list, start=1):
                number = it.get("number") or i
                title = it.get("title")
                category = it.get("category") or "Outro"
                if title:
                    exec_sql("""
                        INSERT OR IGNORE INTO themes(number, title, category, status)
                        VALUES(:num, :title, :cat, 'livre')
                    """, num=number, title=title, cat=category)

# ===================== Integração com SharePoint (Graph API) =====================
import msal

def graph_token() -> Optional[str]:
    aad = st.secrets.get("aad") or st.secrets
    tenant = aad.get("TENANT_ID"); client_id = aad.get("CLIENT_ID"); secret = aad.get("CLIENT_SECRET")
    if not (tenant and client_id and secret):
        return None
    app = msal.ConfidentialClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant}", client_credential=secret)
    scopes = ["https://graph.microsoft.com/.default"]
    try:
        result = app.acquire_token_for_client(scopes=scopes)
        return result.get("access_token")
    except Exception:
        return None

def upload_to_sharepoint(local_path: str, remote_name: str) -> bool:
    token = graph_token()
    if not token:
        return False
    sp = st.secrets.get("sharepoint") or st.secrets
    site_url = sp.get("SP_SITE_URL") or sp.get("SITE_URL")
    drive_name = sp.get("SP_DRIVE_NAME") or sp.get("DRIVE_NAME")
    base_folder = sp.get("SP_BASE_FOLDER") or sp.get("BASE_FOLDER", "")
    site_id = sp.get("SITE_ID"); drive_id = sp.get("DRIVE_ID")
    # Obtém IDs do site e drive se não fornecidos
    if not site_id or not drive_id:
        if site_url and drive_name:
            try:
                host = site_url.split("//")[1].split("/")[0]
                site_path = site_url.split(host)[-1]
                resp_site = requests.get(f"https://graph.microsoft.com/v1.0/sites/{host}:{site_path}", headers={"Authorization": f"Bearer {token}"})
                if resp_site.status_code == 200:
                    site_data = resp_site.json()
                    site_id = site_data.get("id")
                if site_id:
                    resp_drives = requests.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives", headers={"Authorization": f"Bearer {token}"})
                    if resp_drives.status_code == 200:
                        drives_data = resp_drives.json().get("value", [])
                        for d in drives_data:
                            if d.get("name") == drive_name:
                                drive_id = d.get("id")
                                break
            except Exception:
                site_id = None; drive_id = None
        if site_id and drive_id:
            exec_sql("INSERT OR REPLACE INTO config(key,value) VALUES('SITE_ID', :sid)", sid=site_id)
            exec_sql("INSERT OR REPLACE INTO config(key,value) VALUES('DRIVE_ID', :did)", did=drive_id)
    else:
        df_site = get_df("SELECT value FROM config WHERE key='SITE_ID'")
        df_drive = get_df("SELECT value FROM config WHERE key='DRIVE_ID'")
        if df_site.empty or df_drive.empty:
            exec_sql("INSERT OR REPLACE INTO config(key,value) VALUES('SITE_ID', :sid)", sid=site_id)
            exec_sql("INSERT OR REPLACE INTO config(key,value) VALUES('DRIVE_ID', :did)", did=drive_id)
    if not site_id or not drive_id:
        return False
    folder_path = base_folder.strip("/")
    target_path = f"{folder_path}/{remote_name}" if folder_path else remote_name
    url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/root:/{target_path}:/content"
    try:
        with open(local_path, "rb") as f:
            resp = requests.put(url, headers={"Authorization": f"Bearer {token}"}, data=f)
        return resp.status_code in (200, 201)
    except Exception:
        return False

# ===================== Funções auxiliares de negócio =====================
def get_student_group(student_name: str) -> Optional[str]:
    df = get_df("""
        SELECT g.code FROM groups g 
        JOIN group_members gm ON gm.group_id = g.id 
        WHERE gm.student_name = :name
    """, name=student_name)
    if df.empty:
        return None
    return df['code'].iloc[0]

def group_member_count(group_code: str) -> int:
    df = get_df("""
        SELECT COUNT(*) as count FROM group_members gm 
        JOIN groups g ON gm.group_id = g.id 
        WHERE g.code = :gc
    """, gc=group_code)
    return int(df['count'].iloc[0]) if not df.empty else 0

# ===================== Autenticação (Login) =====================
if 'auth' not in st.session_state:
    st.session_state['auth'] = {"who": "anon"}

auth = st.session_state['auth']
if auth['who'] == 'anon':
    # Formulário de Login
    st.sidebar.title("Acesso")
    role_choice = st.sidebar.radio("Sou…", ["Aluno", "Docente"], horizontal=True)
    if role_choice == "Aluno":
        ra_input = st.sidebar.text_input("RA")
        email_input = st.sidebar.text_input("E-mail (opcional)")
        if st.sidebar.button("Entrar", key="aluno_login"):
            ra = (ra_input or "").strip()
            if not ra:
                st.sidebar.error("Por favor, insira seu RA.")
            else:
                df_student = get_df("SELECT id, ra, name, email, turma FROM students WHERE ra=:ra AND active=1", ra=ra)
                if df_student.empty:
                    st.sidebar.error("RA não encontrado. Solicite inclusão ao docente.")
                else:
                    student = df_student.iloc[0]
                    # Atualiza email se fornecido e não houver no cadastro
                    if (student['email'] is None or student['email'] == "") and email_input:
                        exec_sql("UPDATE students SET email=:em WHERE id=:id", em=email_input.strip(), id=int(student['id']))
                        student['email'] = email_input.strip()
                    st.session_state['auth'] = {
                        "who": "aluno",
                        "id": int(student['id']),
                        "ra": student['ra'],
                        "name": student['name'],
                        "email": student['email'] or email_input.strip(),
                        "turma": student['turma']
                    }
                    st.rerun()
    else:  # Docente
        email_doc = st.sidebar.text_input("E-mail institucional")
        pin_input = st.sidebar.text_input("PIN", type="password")
        if st.sidebar.button("Entrar", key="prof_login"):
            email_norm = (email_doc or "").strip().lower()
            if not email_norm:
                st.sidebar.error("Por favor, insira seu e-mail.")
            else:
                df_prof = get_df("""
                    SELECT id, name, email, role, pin, approved, discipline_code 
                    FROM professors WHERE lower(email)=lower(:e)
                """, e=email_norm)
                if df_prof.empty:
                    st.sidebar.error("Conta de docente não encontrada. Cadastre na aba Admin.")
                else:
                    prof = df_prof.iloc[0]
                    if int(prof['approved'] or 0) != 1:
                        st.sidebar.warning("Conta de docente pendente de aprovação.")
                    elif (pin_input or "") != (prof['pin'] or ""):
                        st.sidebar.error("PIN inválido.")
                    else:
                        st.session_state['auth'] = {
                            "who": "docente",
                            "id": int(prof['id']),
                            "name": prof['name'],
                            "email": prof['email'],
                            "role": prof['role'],
                            "disc": prof['discipline_code']
                        }
                        st.rerun()
else:
    # Usuário logado
    if auth['who'] == 'aluno':
        st.write(f"# Bem-vindo, {auth['name']}!")
        group_code = get_student_group(auth['name'])
        if not group_code:
            st.warning("Você ainda não está em um grupo. Consulte o docente para definir seu grupo.")
        else:
            # Informações do grupo do aluno
            df_group_theme = get_df("SELECT theme_title, submitted_at, submitted_by FROM submissions WHERE group_code = :gc", gc=group_code)
            theme_reserved = None
            if not df_group_theme.empty:
                theme_reserved = df_group_theme['theme_title'].iloc[0] or None
            st.write(f"**Grupo:** {group_code}" + (f" – Tema reservado: {theme_reserved}" if theme_reserved else ""))
            df_members = get_df("""
                SELECT student_name FROM group_members gm 
                JOIN groups g ON gm.group_id = g.id 
                WHERE g.code = :gc
            """, gc=group_code)
            if not df_members.empty:
                members_list = df_members['student_name'].tolist()
                st.write(f"**Membros do grupo:** {', '.join(members_list)}")
            # Reserva de tema
            st.subheader("Reserva de Tema")
            df_themes_avail = get_df("SELECT title, category FROM themes WHERE status='livre'")
            if df_themes_avail.empty:
                st.info("Todos os temas já foram reservados.")
            else:
                theme_options = df_themes_avail['title'].tolist()
                selected_theme = st.selectbox("Escolha um tema disponível:", ["(selecione)"] + theme_options, key="theme_select_student")
                deadline_dt = None
                try:
                    deadline_dt = datetime.fromisoformat(RESERVE_DEADLINE)
                except Exception:
                    pass
                if st.button("Reservar Tema"):
                    if selected_theme and selected_theme != "(selecione)":
                        count = group_member_count(group_code)
                        now = datetime.now()
                        if count < 5 and deadline_dt and now < deadline_dt:
                            st.error(f"Grupos com menos de 5 alunos só podem reservar temas após {deadline_dt.strftime('%d/%m/%Y')}.")
                        else:
                            exec_sql("UPDATE themes SET status='reservado', reserved_by=:gc, reserved_at=:ts WHERE title=:t AND status='livre'",
                                    gc=group_code, ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), t=selected_theme)
                            theme_reserved = selected_theme
                            st.success(f"Tema **{selected_theme}** reservado com sucesso!")
                            st.rerun()
            # Submissão dos entregáveis
            st.subheader("Submissão dos Entregáveis")
            if not df_group_theme.empty:
                sub_info = df_group_theme.iloc[0]
                st.info(f"Este grupo já submeteu o trabalho em {sub_info['submitted_at']} (por {sub_info['submitted_by']}).")
                st.write("Arquivos enviados:")
                df_files = get_df("SELECT report_path, slides_path, zip_path, media_link, media_file_path FROM submissions WHERE group_code=:gc", gc=group_code)
                if not df_files.empty:
                    files = df_files.iloc[0]
                    if files['report_path']:
                        with open(files['report_path'], 'rb') as f:
                            st.download_button("Baixar Relatório", f, file_name=os.path.basename(files['report_path']))
                    if files['slides_path']:
                        with open(files['slides_path'], 'rb') as f:
                            st.download_button("Baixar Slides", f, file_name=os.path.basename(files['slides_path']))
                    if files['zip_path']:
                        with open(files['zip_path'], 'rb') as f:
                            st.download_button("Baixar Materiais Adicionais", f, file_name=os.path.basename(files['zip_path']))
                    if files['media_file_path']:
                        with open(files['media_file_path'], 'rb') as f:
                            st.download_button("Baixar Mídia", f, file_name=os.path.basename(files['media_file_path']))
                    if files['media_link']:
                        st.write(f"[Link do Vídeo]({files['media_link']})")
                st.write("Caso precise atualizar a submissão, entre em contato com o docente.")
            else:
                st.write("Envie os arquivos para cada entregável:")
                report_file = st.file_uploader("Relatório (PDF)", type=["pdf"])
                slides_file = st.file_uploader("Apresentação (PPTX ou PDF)", type=["pptx", "pdf"])
                bundle_file = st.file_uploader("Materiais adicionais (ZIP)", type=["zip"])
                colL, colR = st.columns(2)
                with colL:
                    media_link = st.text_input("Link do Vídeo (YouTube ou OneDrive compartilhado)")
                with colR:
                    media_upload = st.file_uploader("Ou enviar arquivo de mídia", type=["mp4", "mp3", "m4a", "wav", "mov"])
                consent = st.checkbox("Autorizo a publicação do trabalho se for selecionado entre os melhores.")
                if st.button("Enviar Submissão", type="primary"):
                    if not report_file or not slides_file or ((not media_link) and (not media_upload)):
                        st.error("Relatório, slides e um vídeo (link ou arquivo) são obrigatórios.")
                    else:
                        if theme_reserved is None and (selected_theme is None or selected_theme == "" or selected_theme == "(selecione)"):
                            st.error("É necessário selecionar/reservar um tema antes da submissão.")
                        else:
                            # Salva arquivos localmente
                            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                            report_path = slides_path = zip_path = media_file_path = ""
                            if report_file:
                                report_fname = f"{group_code}_relatorio_{timestamp}.pdf"
                                report_path = os.path.join(UPLOAD_DIR, report_fname)
                                with open(report_path, "wb") as f:
                                    f.write(report_file.getbuffer())
                            if slides_file:
                                ext = os.path.splitext(slides_file.name)[1] or ".pptx"
                                slides_fname = f"{group_code}_slides_{timestamp}{ext}"
                                slides_path = os.path.join(UPLOAD_DIR, slides_fname)
                                with open(slides_path, "wb") as f:
                                    f.write(slides_file.getbuffer())
                            if bundle_file:
                                zip_fname = f"{group_code}_material_{timestamp}.zip"
                                zip_path = os.path.join(UPLOAD_DIR, zip_fname)
                                with open(zip_path, "wb") as f:
                                    f.write(bundle_file.getbuffer())
                            media_link_str = media_link.strip()
                            if media_upload:
                                ext = os.path.splitext(media_upload.name)[1] or ".mp4"
                                media_fname = f"{group_code}_media_{timestamp}{ext}"
                                media_file_path = os.path.join(UPLOAD_DIR, media_fname)
                                with open(media_file_path, "wb") as f:
                                    f.write(media_upload.getbuffer())
                            exec_sql("""
                                INSERT INTO submissions(group_code, theme_title, report_path, slides_path, zip_path, media_link, media_file_path, consent, submitted_by, submitted_at)
                                VALUES(:gc, :theme, :rp, :sp, :zp, :ml, :mf, :cons, :by, :at)
                            """, gc=group_code,
                                   theme=(selected_theme if selected_theme and selected_theme != "(selecione)" else theme_reserved) or "",
                                   rp=report_path, sp=slides_path, zp=zip_path,
                                   ml=media_link_str, mf=media_file_path,
                                   cons=1 if consent else 0, by=auth['name'], at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                            if selected_theme and selected_theme not in (None, "", "(selecione)"):
                                exec_sql("UPDATE themes SET status='reservado', reserved_by=:gc, reserved_at=:ts WHERE title=:t",
                                        gc=group_code, ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), t=selected_theme)
                            st.success("Trabalho submetido com sucesso!")
                            # Upload para SharePoint (backup)
                            if report_path:
                                upload_to_sharepoint(report_path, os.path.basename(report_path))
                            if slides_path:
                                upload_to_sharepoint(slides_path, os.path.basename(slides_path))
                            if zip_path:
                                upload_to_sharepoint(zip_path, os.path.basename(zip_path))
                            if media_file_path:
                                upload_to_sharepoint(media_file_path, os.path.basename(media_file_path))
                            st.rerun()
    elif auth['who'] == 'docente':
        is_admin = (auth.get('role') == 'admin')
        st.write(f"# Olá, Prof. {auth['name']}!")
        tabs = ["Avaliações", "Dashboard"]
        if is_admin:
            tabs.append("Admin")
        tab_sel = st.tabs(tabs)
        # Aba Avaliações
        with tab_sel[0]:
            st.subheader("Avaliação dos Trabalhos")
            df_subs = get_df("SELECT id, group_code, theme_title, submitted_at FROM submissions ORDER BY group_code")
            if df_subs.empty:
                st.write("Nenhuma submissão realizada ainda.")
            else:
                class_options = []
                df_classes = get_df("SELECT DISTINCT turma FROM groups")
                if not df_classes.empty:
                    class_list = [c for c in df_classes['turma'] if c]
                    if class_list:
                        class_options = ["Todas"] + sorted(class_list)
                selected_class = None
                if class_options:
                    selected_class = st.selectbox("Turma", class_options, key="class_filter_avaliacao")
                if is_admin:
                    disc_options = ["Todas", "IND", "EBCII"]
                    selected_disc = st.selectbox("Disciplina", disc_options, key="disc_filter_avaliacao")
                else:
                    selected_disc = auth['disc']
                filtered_subs = df_subs
                if selected_class and selected_class != "Todas":
                    filtered_subs = get_df("""
                        SELECT s.id, s.group_code, s.theme_title, s.submitted_at 
                        FROM submissions s JOIN groups g ON s.group_code = g.code 
                        WHERE g.turma = :turma
                        ORDER BY s.group_code
                    """, turma=selected_class)
                if selected_disc and selected_disc not in ("Todas", "JOINT"):
                    # Em projeto integrado, não filtramos submissões por disciplina (todas são conjuntas)
                    pass
                submission_list = [f"Grupo {row['group_code']} – {row['theme_title']}" for _, row in filtered_subs.iterrows()]
                if not submission_list:
                    st.write("Nenhuma submissão encontrada.")
                else:
                    selected_index = st.selectbox("Selecione um Grupo:", range(len(submission_list)), format_func=lambda i: submission_list[i], key="select_group_eval")
                    sub_id = int(filtered_subs.iloc[selected_index]['id'])
                    group_code = filtered_subs.iloc[selected_index]['group_code']
                    theme_title = filtered_subs.iloc[selected_index]['theme_title']
                    submitted_at = filtered_subs.iloc[selected_index]['submitted_at']
                    st.write(f"**Grupo {group_code} – Tema:** {theme_title}")
                    st.write(f"**Enviado em:** {submitted_at}")
                    df_members = get_df("""
                        SELECT gm.student_name FROM group_members gm 
                        JOIN groups g ON gm.group_id = g.id 
                        WHERE g.code = :gc
                    """, gc=group_code)
                    if not df_members.empty:
                        st.write(f"**Integrantes:** {', '.join(df_members['student_name'])}")
                    df_files = get_df("SELECT report_path, slides_path, zip_path, media_link, media_file_path FROM submissions WHERE id=:id", id=sub_id)
                    if not df_files.empty:
                        files = df_files.iloc[0]
                        st.write("**Arquivos:**")
                        if files['report_path']:
                            with open(files['report_path'], 'rb') as f:
                                st.download_button("Relatório", f, file_name=os.path.basename(files['report_path']), key=f"down_rep_{sub_id}")
                        if files['slides_path']:
                            with open(files['slides_path'], 'rb') as f:
                                st.download_button("Slides", f, file_name=os.path.basename(files['slides_path']), key=f"down_sld_{sub_id}")
                        if files['zip_path']:
                            with open(files['zip_path'], 'rb') as f:
                                st.download_button("Material Adicional", f, file_name=os.path.basename(files['zip_path']), key=f"down_zip_{sub_id}")
                        if files['media_file_path']:
                            with open(files['media_file_path'], 'rb') as f:
                                st.download_button("Mídia", f, file_name=os.path.basename(files['media_file_path']), key=f"down_media_{sub_id}")
                        if files['media_link']:
                            st.write(f"[Vídeo]({files['media_link']})")
                    st.markdown("---")
                    st.write("### Avaliação:")
                    df_eval = get_df("""
                        SELECT score_report, score_slides, score_media, overall_score, c_report, c_slides, c_media, c_overall 
                        FROM evaluations 
                        WHERE submission_id=:sid AND instructor_id=:iid
                    """, sid=sub_id, iid=auth['id'])
                    existing_eval = None if df_eval.empty else df_eval.iloc[0]
                    score_report = st.slider("Nota – Relatório Escrito (0-10)", 0.0, 10.0, float(existing_eval['score_report']) if existing_eval is not None else 0.0, 0.5)
                    score_slides = st.slider("Nota – Slides/Apresentação (0-10)", 0.0, 10.0, float(existing_eval['score_slides']) if existing_eval is not None else 0.0, 0.5)
                    score_media = st.slider("Nota – Vídeo (0-10)", 0.0, 10.0, float(existing_eval['score_media']) if existing_eval is not None else 0.0, 0.5)
                    overall_score = st.slider("Nota Geral (0-10)", 0.0, 10.0, float(existing_eval['overall_score']) if existing_eval is not None else 0.0, 0.5)
                    c_report = st.text_area("Comentários – Relatório", existing_eval['c_report'] if existing_eval is not None else "")
                    c_slides = st.text_area("Comentários – Slides/Apresentação", existing_eval['c_slides'] if existing_eval is not None else "")
                    c_media = st.text_area("Comentários – Vídeo", existing_eval['c_media'] if existing_eval is not None else "")
                    c_overall = st.text_area("Comentários Gerais", existing_eval['c_overall'] if existing_eval is not None else "")
                    df_other_evals = get_df("""
                        SELECT p.name, e.discipline_code, e.overall_score, e.c_overall 
                        FROM evaluations e JOIN professors p ON e.instructor_id = p.id 
                        WHERE e.submission_id = :sid AND e.instructor_id != :iid
                    """, sid=sub_id, iid=auth['id'])
                    if not df_other_evals.empty:
                        st.write("### Notas de outros docentes:")
                        for _, ev in df_other_evals.iterrows():
                            st.write(f"**{ev['name']} ({ev['discipline_code']}):** Nota Geral = {ev['overall_score']}")
                            if ev['c_overall']:
                                st.write(f"💬 {ev['c_overall']}")
                    if st.button("Salvar Avaliação", key=f"save_eval_{sub_id}"):
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        if existing_eval is None:
                            exec_sql("""
                                INSERT INTO evaluations(submission_id, instructor_id, discipline_code, score_report, score_slides, score_media, overall_score, liked, c_report, c_slides, c_media, c_overall, created_at) 
                                VALUES(:sid, :iid, :disc, :sr, :ss, :sm, :os, 0, :cr, :cs, :cm, :co, :at)
                            """, sid=sub_id, iid=auth['id'], disc=auth['disc'],
                                   sr=score_report, ss=score_slides, sm=score_media, os=overall_score,
                                   cr=c_report.strip(), cs=c_slides.strip(), cm=c_media.strip(), co=c_overall.strip(), at=now_str)
                        else:
                            exec_sql("""
                                UPDATE evaluations 
                                SET score_report=:sr, score_slides=:ss, score_media=:sm, overall_score=:os,
                                    c_report=:cr, c_slides=:cs, c_media=:cm, c_overall=:co, created_at=:at
                                WHERE submission_id=:sid AND instructor_id=:iid
                            """, sr=score_report, ss=score_slides, sm=score_media, os=overall_score,
                                   cr=c_report.strip(), cs=c_slides.strip(), cm=c_media.strip(), co=c_overall.strip(),
                                   at=now_str, sid=sub_id, iid=auth['id'])
                        st.success("Avaliação salva com sucesso!")
        # Aba Dashboard
        with tab_sel[1]:
            st.subheader("Painel de Acompanhamento")
            df_total_groups = get_df("SELECT COUNT(*) as total FROM groups")
            total_groups = int(df_total_groups['total'][0]) if not df_total_groups.empty else 0
            df_reserved = get_df("SELECT COUNT(DISTINCT reserved_by) as reserved FROM themes WHERE status='reservado'")
            reserved_count = int(df_reserved['reserved'][0]) if not df_reserved.empty else 0
            df_submitted = get_df("SELECT COUNT(DISTINCT group_code) as submitted FROM submissions")
            submitted_count = int(df_submitted['submitted'][0]) if not df_submitted.empty else 0
            df_evaluated = get_df("""
                SELECT s.group_code 
                FROM submissions s 
                JOIN evaluations e ON s.id = e.submission_id 
                GROUP BY s.group_code 
                HAVING COUNT(DISTINCT e.discipline_code) >= 2
            """)
            evaluated_count = len(df_evaluated) if not df_evaluated.empty else 0
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Grupos formados", total_groups)
            col2.metric("Temas reservados", reserved_count)
            col3.metric("Trabalhos submetidos", submitted_count)
            col4.metric("Trabalhos avaliados (ambas disciplinas)", evaluated_count)
            if reserved_count < total_groups:
                st.write(f"**Grupos sem tema:** {total_groups - reserved_count}")
            if submitted_count < reserved_count:
                st.write(f"**Grupos com tema mas não submetido:** {reserved_count - submitted_count}")
            if evaluated_count < submitted_count:
                st.write(f"**Submissões pendentes de avaliação:** {submitted_count - evaluated_count}")
        # Aba Admin (para admin)
        if is_admin:
            with tab_sel[2]:
                st.subheader("Administração")
                st.write("### Gerenciar Temas")
                with st.form(key="add_theme_form"):
                    col1, col2 = st.columns([3, 2])
                    with col1:
                        new_theme_title = st.text_input("Título do novo tema")
                    with col2:
                        new_theme_cat = st.text_input("Categoria", value="Outro")
                    submit_theme = st.form_submit_button("Adicionar Tema")
                    if submit_theme:
                        title = new_theme_title.strip()
                        if title:
                            exec_sql("INSERT OR IGNORE INTO themes(number, title, category, status) VALUES(NULL, :t, :c, 'livre')",
                                    t=title, c=new_theme_cat.strip() or "Outro")
                            st.success(f"Tema '{title}' adicionado.")
                        else:
                            st.error("O título do tema não pode estar vazio.")
                st.write("### Gerenciar Alunos")
                with st.form(key="add_student_form"):
                    col1, col2 = st.columns(2)
                    with col1:
                        new_st_ra = st.text_input("RA do Aluno")
                        new_st_name = st.text_input("Nome do Aluno")
                    with col2:
                        new_st_email = st.text_input("E-mail do Aluno")
                        new_st_class = st.text_input("Turma (ex: MA6)")
                    submit_student = st.form_submit_button("Adicionar Aluno")
                    if submit_student:
                        ra = new_st_ra.strip()
                        name = new_st_name.strip()
                        email = new_st_email.strip()
                        if not ra or not name:
                            st.error("RA e Nome são obrigatórios.")
                        else:
                            try:
                                exec_sql("INSERT OR IGNORE INTO students(ra, name, email, turma, course_code, active) VALUES(:ra, :name, :email, :turma, NULL, 1)",
                                        ra=ra, name=name, email=email, turma=new_st_class.strip())
                                st.success(f"Aluno {name} (RA {ra}) adicionado.")
                            except Exception:
                                st.error("Erro ao adicionar aluno. Verifique se o RA já existe.")
                st.write("### Gerenciar Docentes")
                with st.form(key="add_prof_form"):
                    col1, col2, col3 = st.columns([3, 3, 2])
                    with col1:
                        new_prof_name = st.text_input("Nome do Docente")
                    with col2:
                        new_prof_email = st.text_input("E-mail do Docente")
                    with col3:
                        new_prof_disc = st.selectbox("Disciplina", ["IND", "EBCII"])
                    col4, col5 = st.columns([2, 2])
                    with col4:
                        new_prof_pin = st.text_input("PIN (4 dígitos, opcional)")
                    with col5:
                        approve_now = st.checkbox("Aprovar agora", value=True)
                    submit_prof = st.form_submit_button("Adicionar Docente")
                    if submit_prof:
                        name = new_prof_name.strip()
                        email = new_prof_email.strip().lower()
                        if not name or not email:
                            st.error("Nome e e-mail são obrigatórios.")
                        else:
                            try:
                                exec_sql("""
                                    INSERT INTO professors(name, email, role, pin, approved, discipline_code, created_at) 
                                    VALUES(:name, :email, 'docente', :pin, :app, :disc, :at)
                                """, name=name, email=email, pin=new_prof_pin.strip(), app=1 if approve_now else 0,
                                       disc=new_prof_disc, at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                                st.success(f"Docente {name} adicionado.")
                            except Exception:
                                st.error("Erro ao adicionar docente. Verifique se o e-mail já está cadastrado.")
                df_pending = get_df("SELECT name, email FROM professors WHERE approved=0")
                if not df_pending.empty:
                    st.write("### Docentes pendentes de aprovação:")
                    for _, row in df_pending.iterrows():
                        pname = row['name']; pemail = row['email']
                        if st.button(f"Aprovar {pname} ({pemail})", key=f"approve_{pemail}"):
                            exec_sql("UPDATE professors SET approved=1 WHERE email=:email", email=pemail)
                            st.success(f"Docente {pname} aprovado.")
                            st.rerun()
                st.write("### Relatórios Exportáveis")
                # Relatório por Grupo
                df_groups = get_df("""
                    SELECT g.code AS Grupo, 
                           COALESCE(s.theme_title, '') AS Tema, 
                           GROUP_CONCAT(gm.student_name, ', ') AS Integrantes,
                           CASE WHEN s.id IS NOT NULL THEN 'Sim' ELSE 'Não' END AS Submeteu,
                           MAX(CASE WHEN e.discipline_code='IND' THEN e.overall_score END) AS Nota_Industrial,
                           MAX(CASE WHEN e.discipline_code='EBCII' THEN e.overall_score END) AS Nota_EBCII
                    FROM groups g 
                    LEFT JOIN group_members gm ON gm.group_id = g.id
                    LEFT JOIN submissions s ON s.group_code = g.code
                    LEFT JOIN evaluations e ON e.submission_id = s.id
                    GROUP BY g.code
                """)
                csv_groups = df_groups.to_csv(index=False).encode("utf-8")
                st.download_button("Baixar CSV – Por Grupo", data=csv_groups, file_name="relatorio_grupos.csv", mime="text/csv")
                # Relatório por Aluno
                df_students = get_df("""
                    SELECT st.ra AS RA, st.name AS Nome, g.code AS Grupo, COALESCE(s.theme_title, '') AS Tema,
                           CASE WHEN s.id IS NOT NULL THEN 'Sim' ELSE 'Não' END AS Submeteu,
                           (SELECT e.overall_score FROM evaluations e JOIN professors pr ON e.instructor_id=pr.id 
                            WHERE e.submission_id = s.id AND pr.discipline_code='IND' LIMIT 1) AS Nota_Industrial,
                           (SELECT e.overall_score FROM evaluations e JOIN professors pr ON e.instructor_id=pr.id 
                            WHERE e.submission_id = s.id AND pr.discipline_code='EBCII' LIMIT 1) AS Nota_EBCII
                    FROM students st
                    LEFT JOIN group_members gm ON gm.student_name = st.name
                    LEFT JOIN groups g ON gm.group_id = g.id
                    LEFT JOIN submissions s ON s.group_code = g.code
                """)
                csv_students = df_students.to_csv(index=False).encode("utf-8")
                st.download_button("Baixar CSV – Por Aluno", data=csv_students, file_name="relatorio_alunos.csv", mime="text/csv")
                # Relatório por Docente
                df_profs = get_df("""
                    SELECT p.name AS Docente, p.discipline_code AS Disciplina, s.group_code AS Grupo, s.theme_title AS Tema,
                           e.overall_score AS Nota_Atribuida, e.c_overall AS Comentario
                    FROM evaluations e 
                    JOIN professors p ON e.instructor_id = p.id
                    JOIN submissions s ON e.submission_id = s.id
                    ORDER BY p.name, s.group_code
                """)
                csv_profs = df_profs.to_csv(index=False).encode("utf-8")
                st.download_button("Baixar CSV – Por Docente", data=csv_profs, file_name="relatorio_docentes.csv", mime="text/csv")
                # Importação em lote (opcional)
                st.write("### Importar Dados em Lote")
                up_themes = st.file_uploader("Importar Temas (JSON)", type=["json"])
                if up_themes is not None:
                    try:
                        themes_data = json.load(up_themes)
                        added = 0
                        for item in themes_data:
                            title = item.get('title')
                            category = item.get('category', 'Outro')
                            if title:
                                exec_sql("INSERT OR IGNORE INTO themes(number, title, category, status) VALUES(NULL, :t, :c, 'livre')", t=title, c=category)
                                added += 1
                        st.success(f"{added} temas importados.")
                    except Exception:
                        st.error("JSON inválido.")
                up_csv = st.file_uploader("Importar Alunos (CSV)", type=["csv"])
                if up_csv is not None and st.button("Processar CSV"):
                    try:
                        df_csv = pd.read_csv(up_csv)
                        count = 0
                        for _, row in df_csv.iterrows():
                            ra = str(row.get('ra') or row.get('RA') or "").strip()
                            name = str(row.get('name') or row.get('Nome') or row.get('nome') or "").strip()
                            email = str(row.get('email') or row.get('Email') or "").strip()
                            turma = str(row.get('turma') or row.get('Turma') or "").strip()
                            if ra and name:
                                exec_sql("INSERT OR IGNORE INTO students(ra, name, email, turma, course_code, active) VALUES(:ra, :name, :email, :turma, NULL, 1)",
                                        ra=ra, name=name, email=email, turma=turma)
                                count += 1
                        st.success(f"{count} alunos importados via CSV.")
                    except Exception:
                        st.error("Erro ao ler o CSV.")
                up_txts = st.file_uploader("Importar Alunos (TXT PUC)", type=["txt"], accept_multiple_files=True)
                if up_txts:
                    total_added = 0
                    for txt in up_txts:
                        try:
                            content = txt.read().decode("latin-1")
                        except Exception:
                            content = txt.read().decode("utf-8", errors="ignore")
                        matches = re.findall(r"\b(RA\d{8})\b\s+([^\n\r]+)", content)
                        for ra, nm in matches:
                            ra_num = ra.replace("RA", "")
                            name_clean = nm.strip()
                            if ra_num and name_clean:
                                exec_sql("INSERT OR IGNORE INTO students(ra, name, email, turma, course_code, active) VALUES(:ra, :name, '', NULL, NULL, 1)",
                                        ra=ra_num, name=name_clean)
                                total_added += 1
                    st.success(f"{total_added} alunos importados dos TXT(s).")
