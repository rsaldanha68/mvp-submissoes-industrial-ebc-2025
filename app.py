import os, sys, json, pathlib, re
from datetime import datetime
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

# =========================================================
# CONFIGURAÇÃO BÁSICA
# =========================================================
st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

# Pastas e diretórios
DATA_DIR = "data"
UPLOAD_DIR = "uploads"
PUBLIC_DIR = "public"
for p in (DATA_DIR, UPLOAD_DIR, PUBLIC_DIR, "modules"):
    os.makedirs(p, exist_ok=True)

# Incluir pasta de módulos no path (para import_txt, se necessário)
MODULE_DIR = "modules"
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

# Banco de dados (SQLite)
DB_PATH = os.path.join(DATA_DIR, "app.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)

# Parâmetros iniciais
THEMES_FILE = os.path.join(DATA_DIR, "themes_2025_2.json")
DEFAULT_DEADLINE = "2025-03-30T23:59:59"  # Data-limite padrão para reserva de temas

# =========================================================
# MIGRAÇÃO / CRIAÇÃO DE TABELAS
# =========================================================
with engine.begin() as conn:
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS students(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ra TEXT UNIQUE,
            name TEXT NOT NULL,
            email TEXT,
            turma TEXT,
            active INTEGER DEFAULT 1
        );
    """)
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS professors(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            pin TEXT,
            approved INTEGER DEFAULT 0,
            role TEXT CHECK (role IN ('admin','docente')) DEFAULT 'docente'
        );
    """)
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS groups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            turma TEXT,
            created_by TEXT,
            created_at TEXT
        );
    """)
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS group_members(
            group_id INTEGER,
            student_ra TEXT,
            UNIQUE(group_id, student_ra)
        );
    """)
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS themes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number INTEGER UNIQUE,
            title TEXT,
            category TEXT,
            status TEXT DEFAULT 'livre',
            reserved_by TEXT,
            reserved_at TEXT
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
            video_link TEXT,
            consent INTEGER DEFAULT 0,
            submitted_by TEXT,
            submitted_at TEXT,
            approved INTEGER DEFAULT 0
        );
    """)
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS reviews(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER,
            instructor_id INTEGER,
            score REAL,
            liked INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(submission_id, instructor_id)
        );
    """)
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS config(
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

# Insere usuário admin padrão (professor rsaldanha@pucsp.br) se não existir
ADMIN_EMAIL = st.secrets.get("ADMIN_EMAIL", os.getenv("ADMIN_EMAIL", "rsaldanha@pucsp.br"))
ADMIN_PIN   = st.secrets.get("ADMIN_PIN",   os.getenv("ADMIN_PIN",   "8722"))
with engine.begin() as conn:
    # Garante que haja pelo menos um administrador aprovado
    row = conn.execute(text("SELECT 1 FROM professors WHERE role='admin' AND approved=1 LIMIT 1")).fetchone()
    if not row:
        conn.execute(text("""
            INSERT OR IGNORE INTO professors(name, email, role, pin, approved)
            VALUES(:name, :email, 'admin', :pin, 1)
        """), {"name": "Administrador", "email": ADMIN_EMAIL, "pin": ADMIN_PIN})

# Carrega temas iniciais a partir do JSON, se existir, inserindo temas não cadastrados
try:
    with open(THEMES_FILE, 'r', encoding='utf-8') as f:
        themes_data = json.load(f)
except FileNotFoundError:
    themes_data = []
if themes_data:
    with engine.begin() as conn:
        for theme in themes_data:
            cur = conn.execute(text("SELECT id FROM themes WHERE number = :num"), {"num": theme.get("number")}).fetchone()
            if cur is None:
                conn.execute(text("""
                    INSERT INTO themes(number, title, category, status)
                    VALUES(:num, :title, :cat, 'livre')
                """), {"num": theme.get("number"), "title": theme.get("title"), "cat": theme.get("category", "")})

# Configuração inicial do prazo de reserva de temas no config (caso ainda não esteja definido)
with engine.begin() as conn:
    cur = conn.execute(text("SELECT value FROM config WHERE key = 'theme_reserve_deadline'")).fetchone()
    if cur is None:
        conn.execute(text("INSERT INTO config(key, value) VALUES('theme_reserve_deadline', :val)"), {"val": DEFAULT_DEADLINE})

# =========================================================
# FUNÇÕES AUXILIARES (Banco de dados e Autenticação)
# =========================================================
def get_df(sql, params=None):
    """Executa uma consulta SQL e retorna um DataFrame."""
    params = params or {}
    with engine.begin() as conn:
        df = pd.read_sql(text(sql), conn, params=params)
    return df

def run_sql(sql, params=None):
    """Executa um comando SQL de escrita (INSERT, UPDATE, DELETE)."""
    params = params or {}
    with engine.begin() as conn:
        conn.execute(text(sql), params)

def show_login():
    """Exibe a interface de login para Aluno ou Docente."""
    st.title("Login")
    role_choice = st.radio("Perfil de acesso:", ["Aluno", "Docente"], index=0, horizontal=True)
    if role_choice == "Aluno":
        ra = st.text_input("RA do Aluno")
        name = st.text_input("Nome (completo)")
        if st.button("Entrar", key="student_login"):
            if ra and name:
                df = get_df("SELECT * FROM students WHERE ra = :ra AND name = :name", {"ra": ra.strip(), "name": name.strip()})
                if not df.empty:
                    student = df.iloc[0]
                    st.session_state.user = {
                        "role": "student",
                        "id": int(student["id"]),
                        "ra": student["ra"],
                        "name": student["name"],
                        "turma": student["turma"]
                    }
                    st.success(f"Bem-vindo, {student['name']}!")
                    st.experimental_rerun()
                else:
                    st.error("Dados de aluno não encontrados. Verifique RA e nome.")
            else:
                st.warning("Por favor, preencha RA e nome.")
    else:
        email = st.text_input("E-mail institucional")
        pin = st.text_input("PIN", type="password")
        if st.button("Entrar", key="prof_login"):
            if email and pin:
                df = get_df("SELECT * FROM professors WHERE email = :email", {"email": email.strip()})
                if df.empty:
                    st.error("Conta de docente não encontrada. Solicite acesso abaixo.")
                else:
                    prof = df.iloc[0]
                    if prof["approved"] == 1 and str(prof["pin"]) == str(pin):
                        st.session_state.user = {
                            "role": "professor",
                            "id": int(prof["id"]),
                            "name": prof["name"],
                            "email": prof["email"],
                            "is_admin": (prof["role"] == "admin")
                        }
                        st.success(f"Bem-vindo, Prof. {prof['name']}!")
                        st.experimental_rerun()
                    elif prof["approved"] == 0:
                        st.warning("Seu acesso ainda não foi aprovado pelo administrador.")
                    else:
                        st.error("PIN incorreto. Tente novamente.")
            else:
                st.warning("Por favor, preencha e-mail e PIN.")
        st.write("---")
        st.write("**Solicitar acesso de Docente:**")
        name_req = st.text_input("Nome do Docente", key="prof_name_req")
        email_req = st.text_input("E-mail institucional (@pucsp.br)", key="prof_email_req")
        pin_req = st.text_input("Escolha um PIN (senha numérica)", key="prof_pin_req")
        if st.button("Solicitar acesso", key="prof_request"):
            if name_req and email_req and pin_req:
                if "@pucsp.br" not in email_req:
                    st.error("Use um e-mail institucional @pucsp.br.")
                else:
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("""
                                INSERT INTO professors(name, email, pin, approved, role)
                                VALUES(:name, :email, :pin, 0, 'docente')
                            """), {"name": name_req.strip(), "email": email_req.strip(), "pin": pin_req.strip()})
                        st.success("Solicitação enviada. Aguarde aprovação do administrador.")
                    except Exception:
                        st.error("Erro ao solicitar acesso. O e-mail fornecido já está cadastrado.")
            else:
                st.warning("Preencha todos os campos para solicitar acesso.")

# =========================================================
# LÓGICA PRINCIPAL DA APLICAÇÃO
# =========================================================
# Se não há usuário autenticado na sessão, exibe tela de login e interrompe o fluxo
if "user" not in st.session_state:
    show_login()
    st.stop()

# Usuário autenticado
user = st.session_state.user

# Exibe informações do usuário logado e botão de logout na barra lateral
st.sidebar.write(f"Logado como: **{user.get('name', '')}** {'(Admin)' if user.get('is_admin') else ''}")
if st.sidebar.button("Sair"):
    st.session_state.clear()
    st.experimental_rerun()

# Definição das abas disponíveis conforme perfil do usuário
if user["role"] == "student":
    tabs = ["Grupos & Temas", "Upload de Trabalhos"]
else:
    tabs = ["Grupos & Temas", "Upload de Trabalhos", "Galeria/Avaliação", "Administração", "Estudantes & Docentes"]
selected_tab = st.sidebar.selectbox("Ir para:", tabs)

# ======================================
# ABA: Grupos & Temas
# ======================================
if selected_tab == "Grupos & Temas":
    st.header("Grupos & Temas")
    if user["role"] == "student":
        # Verificar se o aluno já está em algum grupo
        df_member = get_df("""
            SELECT g.id, g.code, g.turma 
            FROM groups g 
            JOIN group_members gm ON g.id = gm.group_id 
            WHERE gm.student_ra = :ra
        """, {"ra": user["ra"]})
        if df_member.empty:
            st.info("Você ainda não está em um grupo.")
            # Formulário para criar novo grupo
            st.subheader("Criar novo grupo")
            turmas = get_df("SELECT DISTINCT turma FROM students").dropna()["turma"].tolist()
            turmas.sort()
            turma_escolhida = st.selectbox("Turma do grupo", turmas)
            if st.button("Criar grupo"):
                if turma_escolhida:
                    # Gerar código do grupo automaticamente (prefixo turma + "G" + número sequencial)
                    df_codes = get_df("SELECT code FROM groups WHERE turma = :turma", {"turma": turma_escolhida})
                    existing_codes = df_codes["code"].tolist()
                    prefix = turma_escolhida
                    num = 1
                    if existing_codes:
                        nums = []
                        for code in existing_codes:
                            if code.startswith(prefix + "G"):
                                try:
                                    n = int(code.split("G")[1])
                                    nums.append(n)
                                except ValueError:
                                    continue
                        if nums:
                            num = max(nums) + 1
                    new_code = f"{prefix}G{num}"
                    try:
                        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        with engine.begin() as conn:
                            res = conn.execute(text("""
                                INSERT INTO groups(code, turma, created_by, created_at)
                                VALUES(:code, :turma, :creator, :time)
                            """), {"code": new_code, "turma": turma_escolhida, "creator": user["name"], "time": now})
                            group_id = res.lastrowid
                            conn.execute(text("""
                                INSERT INTO group_members(group_id, student_ra) 
                                VALUES(:gid, :ra)
                            """), {"gid": group_id, "ra": user["ra"]})
                        st.success(f"Grupo criado com código **{new_code}**.")
                        st.experimental_rerun()
                    except Exception:
                        st.error("Não foi possível criar o grupo. Tente novamente.")
                else:
                    st.warning("Selecione a turma do grupo.")
        else:
            # Aluno já participa de um grupo existente
            group_id = int(df_member.iloc[0]["id"])
            group_code = df_member.iloc[0]["code"]
            turma_grupo = df_member.iloc[0]["turma"]
            st.success(f"Você faz parte do grupo **{group_code}** (Turma {turma_grupo}).")
            # Listar membros atuais do grupo
            members_df = get_df("""
                SELECT s.ra, s.name, s.turma 
                FROM students s 
                JOIN group_members gm ON s.ra = gm.student_ra 
                WHERE gm.group_id = :gid
            """, {"gid": group_id})
            if not members_df.empty:
                st.write("**Membros do grupo:**")
                for _, row in members_df.iterrows():
                    st.write(f"- {row['name']} ({row['ra']}) – Turma {row['turma']}")
            # Verificar tamanho do grupo e regras de formação
            count_members = len(members_df)
            if count_members < 3:
                st.warning(f"O grupo possui {count_members} membro{'s' if count_members != 1 else ''}. É necessário ter no mínimo 3 membros.")
            # Formulário para adicionar novo membro (somente se grupo ainda não tem 6 membros)
            if count_members >= 6:
                st.warning("O grupo já possui 6 membros (máximo permitido).")
            else:
                st.subheader("Adicionar membro ao grupo")
                turmas_all = get_df("SELECT DISTINCT turma FROM students").dropna()["turma"].tolist()
                turmas_all.sort()
                turma_filter = st.selectbox("Filtrar por turma", ["(Todas)"] + turmas_all, index=0)
                name_search = st.text_input("Buscar por nome")
                # Obter alunos disponíveis (não alocados em nenhum grupo)
                available_df = get_df("""
                    SELECT s.ra, s.name, s.turma 
                    FROM students s
                    WHERE s.active = 1
                      AND NOT EXISTS (
                          SELECT 1 FROM group_members gm WHERE gm.student_ra = s.ra
                      )
                """)
                if turma_filter and turma_filter != "(Todas)":
                    available_df = available_df[available_df["turma"] == turma_filter]
                if name_search:
                    available_df = available_df[available_df["name"].str.contains(name_search, case=False)]
                if available_df.empty:
                    st.write("Nenhum aluno disponível encontrado com esse filtro.")
                else:
                    options = available_df.apply(lambda r: f"{r['name']} ({r['ra']}, {r['turma']})", axis=1).tolist()
                    selection = st.selectbox("Selecionar aluno", [""] + options, index=0)
                    sel_ra = None
                    if selection:
                        m = re.search(r"\(([^,]+),", selection)
                        if m:
                            sel_ra = m.group(1).strip()
                    if st.button("Adicionar ao grupo"):
                        if sel_ra:
                            try:
                                with engine.begin() as conn:
                                    conn.execute(text("""
                                        INSERT INTO group_members(group_id, student_ra)
                                        VALUES(:gid, :ra)
                                    """), {"gid": group_id, "ra": sel_ra})
                                st.success("Aluno adicionado ao grupo com sucesso.")
                                st.experimental_rerun()
                            except Exception:
                                st.error("Não foi possível adicionar. Verifique se o aluno já está em outro grupo.")
                        else:
                            st.warning("Selecione um aluno para adicionar.")
            # Seção de reserva de tema
            st.subheader("Reserva de Tema")
            theme_row = get_df("SELECT title FROM themes WHERE reserved_by = :gc", {"gc": group_code})
            if not theme_row.empty:
                reserved_title = theme_row.iloc[0]["title"]
                st.info(f"Tema já reservado pelo grupo: **{reserved_title}**")
            else:
                # Verificar condições para habilitar reserva de tema
                count_members = get_df("SELECT COUNT(*) AS cnt FROM group_members WHERE group_id = :gid", {"gid": group_id}).iloc[0]["cnt"]
                deadline_str = get_df("SELECT value FROM config WHERE key = 'theme_reserve_deadline'").iloc[0]["value"]
                deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%dT%H:%M:%S")
                now_dt = datetime.now()
                if count_members < 5:
                    st.warning("É necessário ter pelo menos 5 membros no grupo para reservar um tema.")
                elif now_dt > deadline_dt:
                    st.warning(f"O prazo para reserva de temas encerrou em {deadline_dt.strftime('%d/%m/%Y %H:%M:%S')}.")
                else:
                    themes_df = get_df("SELECT number, title, category FROM themes WHERE status = 'livre'")
                    if themes_df.empty:
                        st.error("Nenhum tema disponível para reserva.")
                    else:
                        options = themes_df.apply(lambda r: f"{int(r['number'])} – {r['title']}", axis=1).tolist()
                        chosen = st.selectbox("Temas disponíveis:", [""] + options)
                        theme_num = int(chosen.split(" – ")[0]) if chosen else None
                        if st.button("Reservar tema"):
                            if theme_num:
                                try:
                                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    with engine.begin() as conn:
                                        res = conn.execute(text("""
                                            UPDATE themes
                                            SET status = 'reservado',
                                                reserved_by = :gc,
                                                reserved_at = :time
                                            WHERE number = :num AND status = 'livre'
                                        """), {"gc": group_code, "time": now_str, "num": theme_num})
                                        if res.rowcount == 0:
                                            st.error("Tema escolhido não está mais disponível.")
                                        else:
                                            st.success("Tema reservado com sucesso!")
                                            st.experimental_rerun()
                                except Exception:
                                    st.error("Não foi possível reservar o tema.")
                            else:
                                st.warning("Selecione um tema para reservar.")
    else:
        # Visão do docente: lista todos os grupos e temas reservados
        st.subheader("Lista de Grupos")
        groups_df = get_df("""
            SELECT g.code AS Grupo,
                   g.turma AS Turma,
                   (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id = g.id) AS Membros,
                   COALESCE((SELECT title FROM themes t WHERE t.reserved_by = g.code), '—') AS Tema
            FROM groups g
            ORDER BY g.turma, g.code
        """)
        if groups_df.empty:
            st.info("Nenhum grupo criado ainda.")
        else:
            st.dataframe(groups_df, use_container_width=True)

# ======================================
# ABA: Upload de Trabalhos (Submissão)
# ======================================
if selected_tab == "Upload de Trabalhos":
    st.header("Submissão do Trabalho")
    if user["role"] == "student":
        df_member = get_df("""
            SELECT g.code
            FROM groups g 
            JOIN group_members gm ON g.id = gm.group_id
            WHERE gm.student_ra = :ra
        """, {"ra": user["ra"]})
        if df_member.empty:
            st.error("Você ainda não está em um grupo. Forme um grupo para realizar submissões.")
        else:
            group_code = df_member.iloc[0]["code"]
            st.write(f"Grupo: **{group_code}**")
            # Verificar se já existe submissão enviada por este grupo
            sub_df = get_df("SELECT * FROM submissions WHERE group_code = :gc", {"gc": group_code})
            current_sub = sub_df.iloc[0] if not sub_df.empty else None
            if current_sub is not None:
                st.write(f"*(Última submissão em {current_sub['submitted_at']} por {current_sub['submitted_by']})*")
                # Botões para download dos arquivos enviados
                if current_sub["report_path"]:
                    with open(os.path.join(UPLOAD_DIR, current_sub["report_path"]), "rb") as f:
                        st.download_button("📄 Baixar Relatório", f, file_name=current_sub["report_path"])
                if current_sub["slides_path"]:
                    with open(os.path.join(UPLOAD_DIR, current_sub["slides_path"]), "rb") as f:
                        st.download_button("🖥️ Baixar Slides", f, file_name=current_sub["slides_path"])
                if current_sub["zip_path"]:
                    with open(os.path.join(UPLOAD_DIR, current_sub["zip_path"]), "rb") as f:
                        st.download_button("🔗 Baixar Arquivos Adicionais", f, file_name=current_sub["zip_path"])
                if current_sub["video_link"]:
                    st.write(f"🔗 Link do Vídeo: {current_sub['video_link']}")
                st.write("---")
            # Formulário de nova submissão ou atualização
            st.write("**Nova Submissão / Atualizar Submissão**")
            report_file = st.file_uploader("Relatório (PDF)", type=["pdf"])
            slides_file = st.file_uploader("Slides (PDF ou PPT)", type=["pdf", "ppt", "pptx"])
            zip_file = st.file_uploader("Arquivos adicionais (ZIP)", type=["zip"])
            video_link = st.text_input("Link do vídeo (URL)", value=(current_sub["video_link"] if current_sub is not None else ""))
            consent_given = st.checkbox("Declaro que este trabalho pode ser armazenado e divulgado pela PUC-SP", value=False)
            if st.button("Enviar Submissão"):
                if not consent_given:
                    st.error("É necessário concordar com a cessão de direitos para submeter.")
                else:
                    # Salvar arquivos enviados (se novos; caso contrário, manter os anteriores)
                    report_name = current_sub["report_path"] if current_sub is not None else None
                    slides_name = current_sub["slides_path"] if current_sub is not None else None
                    zip_name = current_sub["zip_path"] if current_sub is not None else None
                    if report_file:
                        ext = os.path.splitext(report_file.name)[1]
                        report_name = f"{group_code}_relatorio{ext}"
                        with open(os.path.join(UPLOAD_DIR, report_name), "wb") as f:
                            f.write(report_file.getbuffer())
                    if slides_file:
                        ext = os.path.splitext(slides_file.name)[1]
                        slides_name = f"{group_code}_slides{ext}"
                        with open(os.path.join(UPLOAD_DIR, slides_name), "wb") as f:
                            f.write(slides_file.getbuffer())
                    if zip_file:
                        zip_name = f"{group_code}_extras.zip"
                        with open(os.path.join(UPLOAD_DIR, zip_name), "wb") as f:
                            f.write(zip_file.getbuffer())
                    # Determinar título do tema (se o grupo reservou algum tema)
                    theme_title = None
                    th = get_df("SELECT title FROM themes WHERE reserved_by = :gc", {"gc": group_code})
                    if not th.empty:
                        theme_title = th.iloc[0]["title"]
                    # Registrar submissão (insere nova ou atualiza existente)
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if current_sub is not None:
                        # Atualizar submissão existente (resetando aprovação para 0 novamente)
                        with engine.begin() as conn:
                            conn.execute(text("""
                                UPDATE submissions
                                SET theme_title = :theme,
                                    report_path = :rep,
                                    slides_path = :slides,
                                    zip_path    = :zip,
                                    video_link  = :video,
                                    consent     = 1,
                                    submitted_by = :by,
                                    submitted_at = :at,
                                    approved    = 0
                                WHERE id = :id
                            """), {"theme": theme_title, "rep": report_name, "slides": slides_name, "zip": zip_name,
                                   "video": video_link, "by": user["name"], "at": now_str, "id": int(current_sub["id"])})
                    else:
                        # Inserir nova submissão
                        with engine.begin() as conn:
                            conn.execute(text("""
                                INSERT INTO submissions(group_code, theme_title, report_path, slides_path, zip_path, video_link,
                                                         consent, submitted_by, submitted_at)
                                VALUES(:gc, :theme, :rep, :slides, :zip, :video, 1, :by, :at)
                            """), {"gc": group_code, "theme": theme_title, "rep": report_name, "slides": slides_name,
                                   "zip": zip_name, "video": video_link, "by": user["name"], "at": now_str})
                    st.success("Submissão enviada com sucesso!")
                    st.experimental_rerun()
    else:
        st.info("Esta seção é destinada apenas aos alunos para submissão de trabalhos.")

# ======================================
# ABA: Galeria/Avaliação (Docentes)
# ======================================
if selected_tab == "Galeria/Avaliação":
    st.header("Galeria de Submissões e Avaliação")
    if user["role"] != "professor":
        st.error("Acesso restrito aos docentes.")
    else:
        subs_df = get_df("""
            SELECT id, group_code, theme_title, report_path, slides_path, zip_path, video_link
            FROM submissions
            WHERE approved = 1
            ORDER BY group_code
        """)
        if subs_df.empty:
            st.info("Nenhuma submissão aprovada disponível no momento.")
        else:
            # Exibir cada submissão aprovada com opções de avaliação
            for _, sub in subs_df.iterrows():
                group = sub["group_code"]
                st.subheader(f"Grupo {group}")
                # Informações da submissão
                if sub["theme_title"]:
                    st.write(f"**Tema:** {sub['theme_title']}")
                # Links para download dos arquivos da submissão
                if sub["report_path"]:
                    with open(os.path.join(UPLOAD_DIR, sub["report_path"]), "rb") as f:
                        st.download_button("📄 Relatório", f, file_name=sub["report_path"], key=f"report_{group}")
                if sub["slides_path"]:
                    with open(os.path.join(UPLOAD_DIR, sub["slides_path"]), "rb") as f:
                        st.download_button("🖥️ Slides", f, file_name=sub["slides_path"], key=f"slides_{group}")
                if sub["zip_path"]:
                    with open(os.path.join(UPLOAD_DIR, sub["zip_path"]), "rb") as f:
                        st.download_button("🔗 Arquivos adicionais", f, file_name=sub["zip_path"], key=f"zip_{group}")
                if sub["video_link"]:
                    # Se for um link do YouTube, incorporar player; caso contrário, exibir link
                    if any(host in sub["video_link"] for host in ["youtube.com", "youtu.be"]):
                        st.video(sub["video_link"])
                    else:
                        st.write(f"[🎥 Vídeo]({sub['video_link']})")
                # Carregar avaliação já feita por este docente (se houver)
                rev = get_df("""
                    SELECT score, liked 
                    FROM reviews 
                    WHERE submission_id = :sid AND instructor_id = :iid
                """, {"sid": sub["id"], "iid": user["id"]})
                prev_score = float(rev.iloc[0]["score"]) if not rev.empty and rev.iloc[0]["score"] is not None else 0.0
                prev_liked = bool(rev.iloc[0]["liked"]) if not rev.empty else False
                # Inputs de avaliação
                score_val = st.number_input("Nota (0 a 10)", min_value=0.0, max_value=10.0, step=0.5, value=prev_score, key=f"score_{group}")
                liked_val = st.checkbox("Curtir", value=prev_liked, key=f"liked_{group}")
                if st.button("Salvar Avaliação", key=f"save_{group}"):
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with engine.begin() as conn:
                        cur = conn.execute(text("""
                            SELECT id FROM reviews 
                            WHERE submission_id = :sid AND instructor_id = :iid
                        """), {"sid": sub["id"], "iid": user["id"]}).fetchone()
                        if cur is None:
                            conn.execute(text("""
                                INSERT INTO reviews(submission_id, instructor_id, score, liked, created_at)
                                VALUES(:sid, :iid, :score, :liked, :at)
                            """), {"sid": sub["id"], "iid": user["id"], "score": float(score_val), "liked": 1 if liked_val else 0, "at": now_str})
                        else:
                            conn.execute(text("""
                                UPDATE reviews
                                SET score = :score, liked = :liked, created_at = :at
                                WHERE submission_id = :sid AND instructor_id = :iid
                            """), {"score": float(score_val), "liked": 1 if liked_val else 0, "at": now_str, "sid": sub["id"], "iid": user["id"]})
                    st.success(f"Avaliação do grupo {group} salva!")
            st.write("----")
            st.info("As notas e curtidas acima são registradas individualmente por cada docente.")

# ======================================
# ABA: Administração (Apenas Admin)
# ======================================
if selected_tab == "Administração":
    st.header("Administração do Sistema")
    if not user.get("is_admin", False):
        st.error("Acesso restrito ao administrador.")
    else:
        # Aprovação de submissões pendentes
        st.subheader("Submissões Pendentes de Aprovação")
        pending_subs = get_df("""
            SELECT id, group_code, submitted_at, submitted_by 
            FROM submissions
            WHERE approved = 0
        """)
        if pending_subs.empty:
            st.write("Não há submissões pendentes.")
        else:
            st.dataframe(pending_subs, use_container_width=True)
            to_approve = st.multiselect("Selecionar IDs para aprovar", pending_subs["id"].tolist(), key="sub_approve")
            if st.button("Aprovar selecionadas"):
                if to_approve:
                    with engine.begin() as conn:
                       if to_approve:
                            with engine.begin() as conn:
                                conn.execute(
                                    text("UPDATE submissions SET approved = 1 WHERE id IN :ids"),
                                    {"ids": tuple(to_approve)}
                                  )

                    st.success(f"Aprovadas submissões IDs: {to_approve}")
                    st.experimental_rerun()
        # Aprovação de cadastros de docentes pendentes
        st.subheader("Solicitações de Docentes")
        pending_profs = get_df("SELECT id, name, email FROM professors WHERE approved = 0")
        if pending_profs.empty:
            st.write("Não há solicitações pendentes.")
        else:
            st.dataframe(pending_profs, use_container_width=True)
            to_approve_p = st.multiselect("Selecionar IDs para aprovar docentes", pending_profs["id"].tolist(), key="prof_approve")
            if st.button("Aprovar docentes selecionados"):
                if to_approve_p:
                    with engine.begin() as conn:
                        conn.execute(text(f"""
                            UPDATE professors
                            SET approved = 1
                            WHERE id IN ({','.join(['?'] * len(to_approve_p))})
                        """), tuple(to_approve_p))
                    st.success(f"Aprovados docentes IDs: {to_approve_p}")
                    st.experimental_rerun()
        # Alteração da data-limite de reserva de temas
        st.subheader("Configuração de Data-Limite de Reserva de Temas")
        current_deadline_str = get_df("SELECT value FROM config WHERE key = 'theme_reserve_deadline'").iloc[0]["value"]
        current_deadline = datetime.strptime(current_deadline_str, "%Y-%m-%dT%H:%M:%S")
        new_date = st.date_input("Data", value=current_deadline.date())
        new_time = st.time_input("Hora", value=current_deadline.time())
        if st.button("Atualizar data-limite"):
            new_deadline_str = f"{new_date}T{new_time}"
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE config
                    SET value = :val
                    WHERE key = 'theme_reserve_deadline'
                """), {"val": new_deadline_str})
            st.success("Data-limite atualizada.")
        # Importar lista de alunos via arquivo CSV ou TXT
        st.subheader("Importar Lista de Alunos")
        st.write("Carregue um arquivo CSV (colunas: RA, Nome, Turma) ou um arquivo de lista .txt da PUC-SP:")
        import_file = st.file_uploader("Selecionar arquivo", type=["csv", "txt"])
        if import_file is not None:
            if import_file.name.lower().endswith(".csv"):
                try:
                    df_csv = pd.read_csv(import_file)
                    added = 0
                    for _, row in df_csv.iterrows():
                        ra_val = str(row.get("RA") or row.get("ra") or "").strip()
                        name_val = str(row.get("Nome") or row.get("name") or "").strip()
                        turma_val = str(row.get("Turma") or row.get("turma") or "").strip()
                        if ra_val and name_val:
                            with engine.begin() as conn:
                                conn.execute(text("""
                                    INSERT OR REPLACE INTO students(ra, name, turma, active)
                                    VALUES(:ra, :name, :turma, 1)
                                """), {"ra": ra_val, "name": name_val, "turma": turma_val})
                            added += 1
                    st.success(f"{added} registros de alunos adicionados/atualizados.")
                except Exception:
                    st.error("Falha ao ler o CSV. Verifique o formato esperado.")
            elif import_file.name.lower().endswith(".txt"):
                # Salva o .txt carregado temporariamente
                temp_path = os.path.join(UPLOAD_DIR, "temp_puc_list.txt")
                with open(temp_path, "wb") as f:
                    f.write(import_file.getbuffer())
                try:
                    # O módulo import_txt deve conter função parse_puc_txt para extrair informações da lista .txt
                    from modules import import_txt
                    data = import_txt.parse_puc_txt(temp_path)
                    turma = data.get("turma") or ""
                    students_list = data.get("students", [])
                    added = 0
                    for ra_val, name_val in students_list:
                        if ra_val and name_val:
                            with engine.begin() as conn:
                                conn.execute(text("""
                                    INSERT OR REPLACE INTO students(ra, name, turma, active)
                                    VALUES(:ra, :name, :turma, 1)
                                """), {"ra": ra_val.strip(), "name": name_val.strip(), "turma": turma})
                            added += 1
                    if students_list:
                        st.success(f"{added} alunos importados da turma {turma}.")
                    else:
                        st.warning("Nenhum aluno encontrado no arquivo ou formato inválido.")
                except Exception:
                    st.error("Erro ao processar o arquivo .txt. Verifique se o formato está correto.")
            else:
                st.error("Tipo de arquivo não suportado.")
        # Redefinir PIN de docentes
        st.subheader("Redefinir PIN de Docente")
        profs = get_df("SELECT id, name, email FROM professors WHERE approved = 1")
        if profs.empty:
            st.write("Nenhum docente cadastrado.")
        else:
            options = profs.apply(lambda r: f"{r['name']} ({r['email']})", axis=1).tolist()
            sel_option = st.selectbox("Selecionar docente", [""] + options)
            prof_id = None
            if sel_option:
                # Extrai o índice da seleção (menos 1 porque primeira opção é "")
                prof_index = options.index(sel_option) if sel_option in options else None
                if prof_index is not None:
                    prof_id = int(profs.iloc[prof_index]["id"])
            if st.button("Resetar PIN"):
                if prof_id:
                    # Gera um novo PIN numérico de 6 dígitos
                    new_pin = str(datetime.now().microsecond % 1000000).zfill(6)
                    with engine.begin() as conn:
                        conn.execute(text("UPDATE professors SET pin = :pin WHERE id = :id"), {"pin": new_pin, "id": prof_id})
                        cur = conn.execute(text("SELECT name, email FROM professors WHERE id = :id"), {"id": prof_id}).fetchone()
                    if cur:
                        name_val, email_val = cur
                        st.success(f"PIN de {name_val} redefinido para: **{new_pin}**")
                else:
                    st.warning("Selecione um docente.")

# ======================================
# ABA: Estudantes & Docentes (visão geral para docentes)
# ======================================
if selected_tab == "Estudantes & Docentes":
    st.header("Estudantes & Docentes")
    # Lista de estudantes ativos
    st.subheader("Lista de Estudantes")
    search_stud = st.text_input("Buscar estudante por nome ou RA")
    students_df = get_df("SELECT ra, name, turma FROM students WHERE active = 1")
    if search_stud:
        students_df = students_df[students_df.apply(
            lambda r: search_stud.lower() in str(r['ra']).lower() or search_stud.lower() in str(r['name']).lower(),
            axis=1
        )]
    if students_df.empty:
        st.write("Nenhum estudante encontrado.")
    else:
        st.dataframe(students_df, use_container_width=True)
    # Lista de docentes (todos)
    st.subheader("Lista de Docentes")
    search_prof = st.text_input("Buscar docente por nome ou email")
    profs_df = get_df("SELECT name, email, role, approved FROM professors")
    if search_prof:
        profs_df = profs_df[profs_df.apply(
            lambda r: search_prof.lower() in r['email'].lower() or search_prof.lower() in r['name'].lower(),
            axis=1
        )]
    if profs_df.empty:
        st.write("Nenhum docente encontrado.")
    else:
        # Adicionar coluna de status textual (Aprovado/Pendente)
        profs_display = profs_df.copy()
        profs_display["Status"] = profs_display["approved"].apply(lambda x: "Aprovado" if x == 1 else "Pendente")
        profs_display = profs_display[["name", "email", "role", "Status"]]
        st.dataframe(
            profs_display.rename(columns={"name": "Nome", "email": "E-mail", "role": "Função"}),
            use_container_width=True
        )
