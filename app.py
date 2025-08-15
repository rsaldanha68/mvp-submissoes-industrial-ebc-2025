# app.py - Sistema de Submissão de Trabalhos (Economia Industrial & Economia Brasileira II - PUC-SP)
# Inclui autenticação, áreas de docente e aluno, upload local e para SharePoint, avaliação e geração de relatórios.
# Use `st.secrets` para configurações sensíveis: TENANT_ID, CLIENT_ID, CLIENT_SECRET, ADMIN_EMAIL, ADMIN_PIN, SHAREPOINT_SITE, SHAREPOINT_BASE_FOLDER (e datas limite opcionais).
import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Float, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
import pandas as pd
import datetime, os, io, requests
from fpdf import FPDF

# Configuração inicial do banco de dados SQLite usando SQLAlchemy
Base = declarative_base()
class Student(Base):
    __tablename__ = 'students'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    ra = Column(String, unique=True)        # RA (registro acadêmico) do aluno
    email = Column(String, nullable=True)
    discipline = Column(String)            # Disciplina do aluno
    class_name = Column(String)            # Turma do aluno
    group_id = Column(Integer, ForeignKey('groups.id'))
    group = relationship("Group", back_populates="members")
class Teacher(Base):
    __tablename__ = 'teachers'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    email = Column(String, unique=True)
    pin = Column(String)                   # PIN de autenticação do docente
    discipline = Column(String)            # Disciplina principal do docente
    is_admin = Column(Boolean, default=False)
class Theme(Base):
    __tablename__ = 'themes'
    id = Column(Integer, primary_key=True)
    title = Column(String)
    discipline = Column(String)            # Disciplina à qual o tema pertence
    group = relationship("Group", back_populates="theme", uselist=False)
class Group(Base):
    __tablename__ = 'groups'
    id = Column(Integer, primary_key=True)
    discipline = Column(String)            # Disciplina do grupo (derivada dos alunos)
    class_name = Column(String)            # Turma do grupo
    theme_id = Column(Integer, ForeignKey('themes.id'))
    theme = relationship("Theme", back_populates="group")
    members = relationship("Student", back_populates="group")
    submissions = relationship("Submission", back_populates="group")
class Submission(Base):
    __tablename__ = 'submissions'
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey('groups.id'))
    group = relationship("Group", back_populates="submissions")
    file_report = Column(String, nullable=True)  # caminho do arquivo de relatório salvo
    file_slides = Column(String, nullable=True)  # caminho do arquivo de slides salvo
    file_video = Column(String, nullable=True)   # caminho do arquivo de vídeo salvo
    timestamp = Column(String)                   # data/hora do upload (ISO format)
class Evaluation(Base):
    __tablename__ = 'evaluations'
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey('groups.id'))
    group = relationship("Group")
    teacher_id = Column(Integer, ForeignKey('teachers.id'))
    teacher = relationship("Teacher")
    score1 = Column(Float)   # Nota Critério 1 (Tema e Justificativa)
    score2 = Column(Float)   # Nota Critério 2 (Aplicação dos Conceitos)
    score3 = Column(Float)   # Nota Critério 3 (Análise de Dados)
    score4 = Column(Float)   # Nota Critério 4 (Qualidade do Relatório)
    score5 = Column(Float)   # Nota Critério 5 (Qualidade da Apresentação)
    comment_report = Column(String, nullable=True)
    comment_slides = Column(String, nullable=True)
    comment_video = Column(String, nullable=True)
    comment_general = Column(String, nullable=True)
    approved = Column(Boolean, default=False)    # Aprovado para galeria pública
    eval_timestamp = Column(String)              # data/hora da avaliação (ISO format)

# Inicializa o banco de dados SQLite (arquivo local)
engine = create_engine('sqlite:///submissoes.db')
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)

# Função auxiliar: obter datas-limite para reserva de temas (por disciplina), definidas via st.secrets ou padrão
def get_group_deadline(discipline_name):
    # Retorna um objeto date representando a data limite para a disciplina dada
    try:
        if "Economia Industrial" in discipline_name:
            dstr = st.secrets["EI_DEADLINE"]
        elif "Brasileira" in discipline_name:  # disciplina "Economia Brasileira II"
            dstr = st.secrets["EBII_DEADLINE"]
        else:
            return None
        return datetime.datetime.strptime(dstr, "%Y-%m-%d").date()
    except Exception:
        # Padrões caso não estejam em secrets:
        if "Economia Industrial" in discipline_name:
            return datetime.date(2025, 3, 30)    # data limite padrão para Economia Industrial (30/03/2025)
        elif "Brasileira" in discipline_name:
            return datetime.date(2025, 8, 30)    # data limite padrão para Economia Brasileira II (30/08/2025)
        else:
            return None

# Configurações SharePoint obtidas de st.secrets (credenciais e caminho base)
tenant_id = st.secrets.get("TENANT_ID", None)
client_id = st.secrets.get("CLIENT_ID", None)
client_secret = st.secrets.get("CLIENT_SECRET", None)
sharepoint_site = st.secrets.get("SHAREPOINT_SITE", None)           # ex: "https://<tenant>.sharepoint.com/sites/<SiteName>"
sharepoint_base_folder = st.secrets.get("SHAREPOINT_BASE_FOLDER", "")  # pasta base dentro do Document Library (pode ser vazio ou ex: "Submissoes2025")

# Função auxiliar: obter token de acesso do Microsoft Graph API (usando Client Credentials)
def get_graph_token():
    if not tenant_id or not client_id or not client_secret:
        return None
    # Usamos st.session_state para armazenar token e expiração para reutilizar se possível
    if "graph_token" in st.session_state and "graph_token_expires" in st.session_state:
        expires = st.session_state.graph_token_expires
        if datetime.datetime.utcnow() < expires:
            return st.session_state.graph_token
    # Requisita novo token
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
        'scope': 'https://graph.microsoft.com/.default'
    }
    try:
        resp = requests.post(token_url, data=data)
        resp.raise_for_status()
        token_data = resp.json()
        access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 0)
        # Armazena token e prazo de expiração
        st.session_state.graph_token = access_token
        st.session_state.graph_token_expires = datetime.datetime.utcnow() + datetime.timedelta(seconds=int(expires_in) - 60)
        return access_token
    except Exception as e:
        st.error("Erro ao obter token de acesso do SharePoint: {}".format(e))
        return None

# Função auxiliar: garantir que uma pasta exista no SharePoint (cria se necessário)
def ensure_sharepoint_folder(site_id, folder_path, token):
    # folder_path: caminho dentro do drive (Document Library) onde a pasta deve existir (por ex: "Submissoes2025/Economia Industrial/Grupo_1")
    # A criação é feita recursivamente por níveis
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root"
    # Divide o caminho em partes e cria cada nível se não existir
    parts = folder_path.strip("/").split("/")
    current_path = ""
    for part in parts:
        current_path += f"/{part}"
        url = base_url + f":{current_path}"
        # Verifica se a pasta existe
        res = requests.get(url, headers=headers)
        if res.status_code == 404:
            # Criar pasta
            parent_url = base_url + f":{os.path.dirname(current_path) if os.path.dirname(current_path) != '/' else ''}:/children"
            body = {"name": part, "folder": {}}
            create_res = requests.post(parent_url, headers=headers, json=body)
            if create_res.status_code not in (200, 201, 409):
                # 409 Conflict pode indicar que já existe
                st.error(f"Falha ao criar pasta no SharePoint: {part}")
                return False
    return True

# Função auxiliar: upload de um arquivo para SharePoint (salva no drive do site fornecido)
def upload_file_to_sharepoint(site_id, folder_path, file_name, file_bytes, token):
    # site_id: ID do site SharePoint
    # folder_path: caminho da pasta de destino dentro do drive (sem barra inicial)
    # file_name: nome do arquivo (com extensão) a ser salvo
    # file_bytes: conteúdo em bytes do arquivo
    headers = {"Authorization": f"Bearer {token}"}
    # Monta URL do destino (Graph API)
    if folder_path:
        # Codifica espaços no caminho
        enc_folder = folder_path.replace(" ", "%20")
        upload_url_base = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{enc_folder}"
    else:
        upload_url_base = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root"
    # Se o arquivo for pequeno (<= 4 MB), usamos upload simples
    file_size = len(file_bytes)
    if file_size <= 4 * 1024 * 1024:
        url = upload_url_base + f"/{file_name}:/content"  # upload direto
        try:
            res = requests.put(url, headers=headers, data=file_bytes)
            res.raise_for_status()
            return True
        except Exception as e:
            st.error(f"Erro ao enviar {file_name} para SharePoint: {e}")
            return False
    else:
        # Arquivo grande -> usar Upload Session (envio em partes)
        session_url = upload_url_base + f"/{file_name}:/createUploadSession"
        # Define comportamento para substituir se já existir
        sess_body = {"item": {"@microsoft.graph.conflictBehavior": "replace"}}
        try:
            sess_res = requests.post(session_url, headers=headers, json=sess_body)
            sess_res.raise_for_status()
        except Exception as e:
            st.error(f"Erro ao iniciar envio de {file_name}: {e}")
            return False
        upload_session = sess_res.json()
        upload_url = upload_session.get("uploadUrl")
        if not upload_url:
            st.error("URL de upload de sessão não obtida.")
            return False
        # Envia em blocos
        chunk_size = 5 * 1024 * 1024  # 5 MB
        bytes_io = io.BytesIO(file_bytes)
        total_size = file_size
        bytes_sent = 0
        progress_bar = st.progress(0)  # barra de progresso
        while bytes_sent < total_size:
            chunk = bytes_io.read(chunk_size)
            if not chunk:
                break
            start = bytes_sent
            end = min(bytes_sent + len(chunk) - 1, total_size - 1)
            content_length = end - start + 1
            # Cabeçalho Content-Range
            content_range = f"bytes {start}-{end}/{total_size}"
            chunk_headers = {"Content-Length": str(content_length), "Content-Range": content_range}
            chunk_headers.update(headers)
            try:
                put_res = requests.put(upload_url, headers=chunk_headers, data=chunk)
            except Exception as e:
                st.error(f"Erro no envio de bloco do arquivo {file_name}: {e}")
                return False
            if put_res.status_code in (200, 201):  # upload concluído
                bytes_sent = end + 1
                progress_bar.progress(1.0)  # completa barra de progresso
                break
            elif put_res.status_code == 202:
                bytes_sent = end + 1
                progress = bytes_sent / total_size
                progress_bar.progress(progress)
                # continua até terminar
            else:
                st.error(f"Falha no envio: código {put_res.status_code}")
                return False
        progress_bar.empty()
        return True

# Aplicação Streamlit começa aqui
st.title("📑 Sistema de Submissão de Trabalhos - PUC-SP")

# Sessão de login
if "logged_in" not in st.session_state or not st.session_state.logged_in:
    st.subheader("Login")
    # Seleção do tipo de usuário
    user_type = st.radio("Tipo de usuário", ["Aluno", "Docente"], index=0, horizontal=True)
    # Formulário de login
    if user_type == "Docente":
        # Entrada de email e PIN para docentes
        login_email = st.text_input("Email do Docente", value="", key="login_email")
        login_pin = st.text_input("PIN do Docente", value="", type="password", key="login_pin")
        login_btn = st.button("Entrar")
        if login_btn:
            session = SessionLocal()
            # Verifica se é conta admin (comparando com secrets)
            admin_email = st.secrets.get("ADMIN_EMAIL", None)
            admin_pin = st.secrets.get("ADMIN_PIN", None)
            if admin_email and admin_pin and login_email.strip().lower() == str(admin_email).lower():
                if str(login_pin) == str(admin_pin):
                    # Admin autenticado
                    # Garante que o admin exista na tabela de docentes (senão, cria)
                    teacher = session.query(Teacher).filter(Teacher.email == login_email).first()
                    if not teacher:
                        teacher = Teacher(name="Admin", email=login_email, pin=login_pin, discipline="", is_admin=True)
                        session.add(teacher)
                        session.commit()
                    else:
                        # Atualiza PIN na tabela para refletir secrets atual (opcional)
                        teacher.pin = login_pin
                        teacher.is_admin = True
                        session.commit()
                    st.session_state.logged_in = True
                    st.session_state.user_role = "admin"
                    st.session_state.user_name = teacher.name
                    st.session_state.user_email = teacher.email
                    st.session_state.teacher_id = teacher.id
                    st.session_state.discipline = None  # admin pode ver todas
                    st.experimental_rerun()
                else:
                    st.error("PIN inválido.")
            else:
                # Busca docente no banco de dados
                teacher = session.query(Teacher).filter(Teacher.email == login_email).first()
                if teacher:
                    if teacher.pin == login_pin:
                        # Login docente comum bem-sucedido
                        st.session_state.logged_in = True
                        st.session_state.user_role = "teacher"
                        st.session_state.user_name = teacher.name
                        st.session_state.user_email = teacher.email
                        st.session_state.teacher_id = teacher.id
                        st.session_state.discipline = teacher.discipline
                        st.session_state.is_admin = teacher.is_admin
                        st.experimental_rerun()
                    else:
                        st.error("PIN inválido.")
                else:
                    st.error("Conta de docente não encontrada. Cadastre na interface de administrador.")
            session.close()
    else:
        # Login de Aluno via RA
        login_ra = st.text_input("RA do Aluno", value="", key="login_ra")
        login_btn = st.button("Entrar ", key="login_aluno_btn")
        if login_btn:
            session = SessionLocal()
            student = session.query(Student).filter(Student.ra == login_ra.strip()).first()
            if student:
                st.session_state.logged_in = True
                st.session_state.user_role = "student"
                st.session_state.user_name = student.name
                st.session_state.ra = student.ra
                st.session_state.student_id = student.id
                st.session_state.discipline = student.discipline
                st.session_state.class_name = student.class_name
                # Ao logar aluno, preenche info do grupo (se já tiver)
                st.session_state.group_id = student.group_id
                session.close()
                st.experimental_rerun()
            else:
                st.error("RA não encontrado. Verifique se foi cadastrado.")
                session.close()
else:
    # Se já logado, define variáveis de conveniência
    user_role = st.session_state.user_role
    user_name = st.session_state.get("user_name", "")
    st.write(f"**Usuário:** {user_name} ({'Docente' if user_role in ['teacher','admin'] else 'Aluno'})")
    # Botão de logout
    if st.button("Logout"):
        st.session_state.clear()
        st.experimental_rerun()
    # Tabs de navegação
    if user_role in ["teacher", "admin"]:
        # Docentes (inclui admin) têm acesso a todas as abas
        tab_names = ["Grupos & Temas", "Upload", "Avaliação", "Relatórios", "Administração", "Galeria"]
        tabs = st.tabs(tab_names)
        tab_grupos, tab_upload, tab_avaliacao, tab_relatorios, tab_admin, tab_galeria = tabs
    else:
        # Alunos
        tab_names = ["Grupos & Temas", "Upload", "Galeria"]
        tabs = st.tabs(tab_names)
        tab_grupos, tab_upload, tab_galeria = tabs
        tab_avaliacao = tab_relatorios = tab_admin = None  # não utilizado para aluno

    # Conteúdo da aba "Grupos & Temas"
    with tab_grupos:
        st.subheader("👥 Grupos & Temas")
        session = SessionLocal()
        if user_role == "student":
            # Dados do aluno logado
            student_id = st.session_state.student_id
            student = session.query(Student).get(student_id)
            if not student:
                st.error("Dados do aluno não encontrados.")
            else:
                # Mostra informações do aluno
                st.markdown(f"**Nome:** {student.name}  \n**RA:** {student.ra}  \n**Disciplina:** {student.discipline}  \n**Turma:** {student.class_name}")
                # Verifica se aluno já está em algum grupo
                if student.group:
                    group = student.group
                    st.info(f"Você faz parte do **Grupo {group.id}**.")
                    # Listar membros do grupo
                    members = [m.name for m in group.members]
                    st.write("**Integrantes do grupo:** " + ", ".join(members))
                    # Tema atual do grupo (se definido)
                    if group.theme:
                        st.write(f"**Tema escolhido:** {group.theme.title}")
                    else:
                        st.write("**Tema escolhido:** *(a definir)*")
                    # Se tema ainda não escolhido, permitir seleção conforme regras
                    if not group.theme:
                        # Recupera lista de temas disponíveis (não reservados por outros grupos da mesma disciplina)
                        discipline = student.discipline
                        # Todos temas da disciplina
                        all_themes = session.query(Theme).filter(Theme.discipline == discipline).all()
                        # Filtrar temas já reservados por algum grupo
                        reserved_theme_ids = [g.theme_id for g in session.query(Group).filter(Group.discipline == discipline).all() if g.theme_id]
                        available_themes = [t for t in all_themes if t.id not in reserved_theme_ids]
                        if available_themes:
                            # Regras de reserva: mínimo 4 integrantes antes da data limite
                            deadline_date = get_group_deadline(discipline)
                            if deadline_date:
                                today = datetime.date.today()
                                if today <= deadline_date and len(members) < 4:
                                    st.warning(f"É necessário no mínimo 4 integrantes para reservar um tema antes de {deadline_date.strftime('%d/%m/%Y')}.")
                                else:
                                    # Permite escolher tema
                                    theme_options = {t.title: t for t in available_themes}
                                    chosen_title = st.selectbox("Escolha um Tema para o Grupo", ["(Selecionar)"] + list(theme_options.keys()))
                                    if chosen_title and chosen_title != "(Selecionar)":
                                        chosen_theme = theme_options[chosen_title]
                                        if st.button("Reservar Tema"):
                                            # Associa o tema ao grupo
                                            group.theme = chosen_theme
                                            session.commit()
                                            st.success(f"Tema **{chosen_theme.title}** reservado com sucesso para o Grupo {group.id}.")
                                            st.experimental_rerun()
                        else:
                            st.warning("Nenhum tema disponível para seleção.")
                else:
                    # Aluno ainda não em grupo: permitir criar ou ingressar
                    st.info("Você ainda não está em um grupo.")
                    # Listar grupos existentes na mesma disciplina/turma para permitir ingresso
                    groups_same_class = session.query(Group).filter(Group.discipline == student.discipline, Group.class_name == student.class_name).all()
                    joinable_groups = []
                    for g in groups_same_class:
                        members_count = len(g.members)
                        if members_count < 5:
                            joinable_groups.append((g.id, members_count))
                    if joinable_groups:
                        options = [f"Grupo {gid} (atualmente {count} integrante(s))" for gid, count in joinable_groups]
                    else:
                        options = []
                    col1, col2 = st.columns(2)
                    # Botão de criar novo grupo
                    with col1:
                        if st.button("Criar Novo Grupo"):
                            # Cria um novo grupo para o aluno
                            new_group = Group(discipline=student.discipline, class_name=student.class_name)
                            session.add(new_group)
                            session.flush()  # obtém ID do grupo
                            # Atribui aluno ao novo grupo
                            student.group = new_group
                            session.commit()
                            st.success(f"Grupo {new_group.id} criado e você foi adicionado.")
                            # Atualiza estado de sessão
                            st.session_state.group_id = new_group.id
                            st.experimental_rerun()
                    with col2:
                        selected = st.selectbox("Ingressar em um Grupo existente:", ["(Selecionar)"] + options)
                        if selected and selected != "(Selecionar)":
                            # Extrai o ID do grupo selecionado
                            grp_id = int(selected.split()[1])
                            group = session.query(Group).get(grp_id)
                            if group:
                                # Adiciona aluno ao grupo selecionado
                                student.group = group
                                session.commit()
                                st.success(f"Você ingressou no Grupo {group.id}.")
                                st.session_state.group_id = group.id
                                st.experimental_rerun()
                            else:
                                st.error("Grupo selecionado não encontrado.")
        else:
            # Visão do docente: listar grupos da disciplina (ou de todas, se admin) e seus temas
            st.markdown("**Visualização dos grupos e temas (somente para docentes):**")
            # Se admin, permitir filtrar disciplina
            if user_role == "admin":
                disciplines = session.query(Group.discipline).distinct().all()
                disc_options = ["Todas"] + [d[0] for d in disciplines]
                selected_disc = st.selectbox("Disciplina", disc_options)
            else:
                selected_disc = st.session_state.discipline if st.session_state.discipline else "Todas"
            # Filtra grupos pela disciplina selecionada
            groups_query = session.query(Group)
            if selected_disc and selected_disc != "Todas":
                groups_query = groups_query.filter(Group.discipline == selected_disc)
            groups = groups_query.all()
            if not groups:
                st.write("Nenhum grupo registrado ainda.")
            else:
                # Tabela de grupos com membros e tema
                data = []
                for g in groups:
                    members_names = ", ".join([m.name for m in g.members]) if g.members else ""
                    theme_title = g.theme.title if g.theme else ""
                    data.append({"Grupo": g.id, "Disciplina": g.discipline, "Turma": g.class_name, "Membros": members_names, "Tema": theme_title})
                df = pd.DataFrame(data)
                st.dataframe(df, use_container_width=True)
        session.close()

    # Conteúdo da aba "Upload"
    with tab_upload:
        st.subheader("⬆️ Upload de Entregáveis")
        session = SessionLocal()
        if user_role in ["teacher", "admin"]:
            st.info("Esta seção é apenas para alunos realizarem uploads de seus trabalhos.")
            # Docente pode eventualmente fazer upload em nome de um grupo, se desejar
            # (não solicitado explicitamente, mas poderíamos implementar se necessário)
        else:
            # Aluno - identificamos seu grupo
            student = session.query(Student).get(st.session_state.student_id)
            if not student:
                st.error("Aluno não encontrado no sistema.")
            else:
                group = student.group
                if not group:
                    st.warning("Você ainda não está em um grupo. Cadastre-se em um grupo primeiro na aba 'Grupos & Temas'.")
                else:
                    # Mostra informações do grupo e disciplina
                    st.write(f"**Grupo:** {group.id}  \n**Disciplina:** {group.discipline}  \n**Turma:** {group.class_name}")
                    # Verifica se já existe registro de submissão para este grupo
                    submission = session.query(Submission).filter(Submission.group_id == group.id).first()
                    if submission:
                        # Mostrar arquivos já enviados
                        st.write("Arquivos já enviados:")
                        if submission.file_report:
                            fname = os.path.basename(submission.file_report)
                            st.write(f"- 📄 Relatório: *{fname}*")
                        else:
                            st.write("- 📄 Relatório: *não enviado*")
                        if submission.file_slides:
                            fname = os.path.basename(submission.file_slides)
                            st.write(f"- 📑 Slides: *{fname}*")
                        else:
                            st.write("- 📑 Slides: *não enviados*")
                        if submission.file_video:
                            fname = os.path.basename(submission.file_video)
                            st.write(f"- 🎥 Vídeo: *{fname}*")
                        else:
                            st.write("- 🎥 Vídeo: *não enviado*")
                    else:
                        st.info("Nenhum arquivo enviado ainda para este grupo.")
                    # Formulário de upload de arquivos
                    with st.form("upload_form"):
                        file_report = st.file_uploader("Relatório Escrito (Word/PDF)", type=["pdf", "doc", "docx"], key="file_report")
                        file_slides = st.file_uploader("Slides da Apresentação (PPT/PDF)", type=["ppt", "pptx", "pdf"], key="file_slides")
                        file_video = st.file_uploader("Vídeo da Apresentação (MP4)", type=["mp4", "mov", "avi"], key="file_video")
                        submit_upload = st.form_submit_button("Enviar")
                    if submit_upload:
                        # Realiza upload dos arquivos fornecidos
                        upload_count = 0
                        # Cria diretório local específico do grupo para armazenar arquivos
                        base_dir = "uploads"
                        disc_dir = os.path.join(base_dir, group.discipline.replace(" ", "_"))
                        class_dir = os.path.join(disc_dir, group.class_name.replace(" ", "_"))
                        group_dir = os.path.join(class_dir, f"Grupo_{group.id}")
                        os.makedirs(group_dir, exist_ok=True)
                        # Se não havia submissão, cria registro
                        if not submission:
                            submission = Submission(group=group, timestamp=datetime.datetime.now().isoformat())
                            session.add(submission)
                            session.commit()
                        # Tratamento de cada arquivo
                        if file_report is not None:
                            # Se já havia um arquivo salvo, remove para não duplicar
                            if submission.file_report and os.path.exists(submission.file_report):
                                try:
                                    os.remove(submission.file_report)
                                except OSError:
                                    pass
                            # Salva novo arquivo localmente
                            report_ext = os.path.splitext(file_report.name)[1]
                            report_path = os.path.join(group_dir, f"Relatorio{report_ext}")
                            with open(report_path, "wb") as f:
                                f.write(file_report.getbuffer())
                            submission.file_report = report_path
                            upload_count += 1
                        if file_slides is not None:
                            if submission.file_slides and os.path.exists(submission.file_slides):
                                try:
                                    os.remove(submission.file_slides)
                                except OSError:
                                    pass
                            slides_ext = os.path.splitext(file_slides.name)[1]
                            slides_path = os.path.join(group_dir, f"Slides{slides_ext}")
                            with open(slides_path, "wb") as f:
                                f.write(file_slides.getbuffer())
                            submission.file_slides = slides_path
                            upload_count += 1
                        if file_video is not None:
                            if submission.file_video and os.path.exists(submission.file_video):
                                try:
                                    os.remove(submission.file_video)
                                except OSError:
                                    pass
                            video_ext = os.path.splitext(file_video.name)[1]
                            video_path = os.path.join(group_dir, f"Video{video_ext}")
                            with open(video_path, "wb") as f:
                                f.write(file_video.getbuffer())
                            submission.file_video = video_path
                            upload_count += 1
                        if upload_count == 0:
                            st.warning("Nenhum arquivo selecionado para upload.")
                        else:
                            # Atualiza timestamp de submissão
                            submission.timestamp = datetime.datetime.now().isoformat()
                            session.commit()
                            st.success("Upload realizado com sucesso! Replicando arquivos para SharePoint...")
                            # Upload para SharePoint
                            token = get_graph_token()
                            if token and sharepoint_site:
                                # Obter site_id a partir do URL do site fornecido (se não armazenado em st.session_state)
                                if "sharepoint_site_id" not in st.session_state:
                                    # Extrai hostname e caminho do site
                                    try:
                                        # Ex: https://tenant.sharepoint.com/sites/NomeSite
                                        site_parts = sharepoint_site.split('/')
                                        host = site_parts[2]  # tenant.sharepoint.com
                                        site_path = "/" + "/".join(site_parts[3:])
                                        site_info_url = f"https://graph.microsoft.com/v1.0/sites/{host}:{site_path}"
                                        res = requests.get(site_info_url, headers={"Authorization": f"Bearer {token}"})
                                        res.raise_for_status()
                                        site_id = res.json().get("id", "")
                                        st.session_state.sharepoint_site_id = site_id
                                    except Exception as e:
                                        st.error(f"Falha ao obter ID do site do SharePoint: {e}")
                                        site_id = None
                                else:
                                    site_id = st.session_state.sharepoint_site_id
                                if site_id:
                                    # Monta pasta de destino: base_folder/Disciplina/Turma/Grupo_X
                                    folder_parts = []
                                    if sharepoint_base_folder:
                                        folder_parts.append(sharepoint_base_folder)
                                    folder_parts.append(group.discipline)
                                    folder_parts.append(group.class_name)
                                    folder_parts.append(f"Grupo_{group.id}")
                                    remote_folder = "/".join(folder_parts)
                                    # Garante que a pasta existe
                                    ensured = ensure_sharepoint_folder(site_id, remote_folder, token)
                                    if ensured:
                                        # Faz upload de cada arquivo enviado
                                        if file_report is not None:
                                            file_bytes = file_report.getbuffer().tobytes()
                                            upload_file_to_sharepoint(site_id, remote_folder, f"Relatorio{os.path.splitext(file_report.name)[1]}", file_bytes, token)
                                        if file_slides is not None:
                                            file_bytes = file_slides.getbuffer().tobytes()
                                            upload_file_to_sharepoint(site_id, remote_folder, f"Slides{os.path.splitext(file_slides.name)[1]}", file_bytes, token)
                                        if file_video is not None:
                                            file_bytes = file_video.getbuffer().tobytes()
                                            upload_file_to_sharepoint(site_id, remote_folder, f"Video{os.path.splitext(file_video.name)[1]}", file_bytes, token)
                                        st.info("Arquivos replicados no SharePoint com sucesso.")
                                    else:
                                        st.error("Não foi possível criar/acessar a pasta no SharePoint.")
                            else:
                                st.warning("Credenciais do SharePoint não configuradas. Arquivos salvos apenas localmente.")
                            st.experimental_rerun()  # atualiza a listagem de arquivos enviados
        session.close()

    # Conteúdo da aba "Avaliação" (somente docentes)
    if tab_avaliacao:
        with tab_avaliacao:
            st.subheader("✅ Avaliação de Entregáveis")
            if user_role not in ["teacher", "admin"]:
                st.error("Acesso restrito aos docentes.")
            else:
                session = SessionLocal()
                # Filtros de disciplina e turma (e docente se admin)
                if user_role == "admin":
                    # Filtro de docente
                    teachers = session.query(Teacher).filter(Teacher.discipline != "").all()
                    teacher_options = ["Todos"] + [t.name for t in teachers]
                    selected_teacher_name = st.selectbox("Filtrar por Docente", teacher_options)
                    if selected_teacher_name and selected_teacher_name != "Todos":
                        teacher_obj = next((t for t in teachers if t.name == selected_teacher_name), None)
                    else:
                        teacher_obj = None
                    # Define disciplina filtro baseada no docente escolhido (ou opção para todas)
                    if teacher_obj:
                        disc_filter = teacher_obj.discipline
                        class_options = session.query(Group.class_name).filter(Group.discipline == disc_filter).distinct().all()
                        class_options = ["Todas"] + [c[0] for c in class_options]
                        selected_class = st.selectbox("Turma", class_options)
                    else:
                        disc_options = ["Todas"] + [d[0] for d in session.query(Group.discipline).distinct().all()]
                        disc_filter = st.selectbox("Disciplina", disc_options)
                        class_options = session.query(Group.class_name).filter(Group.discipline == (disc_filter if disc_filter != "Todas" else Group.discipline)).distinct().all()
                        class_options = ["Todas"] + [c[0] for c in class_options]
                        selected_class = st.selectbox("Turma", class_options)
                else:
                    # Docente não-admin: fixar disciplina e permitir filtrar turma se houver mais de uma
                    disc_filter = st.session_state.discipline
                    class_list = session.query(Group.class_name).filter(Group.discipline == disc_filter).distinct().all()
                    class_list = [c[0] for c in class_list]
                    if len(class_list) > 1:
                        class_options = ["Todas"] + class_list
                        selected_class = st.selectbox("Turma", class_options)
                    else:
                        selected_class = class_list[0] if class_list else "Todas"
                        if selected_class != "Todas":
                            st.write(f"Turma: {selected_class}")
                # Preparar lista de grupos para avaliar conforme filtros
                groups_query = session.query(Group)
                if 'disc_filter' in locals() and disc_filter and disc_filter != "Todas":
                    groups_query = groups_query.filter(Group.discipline == disc_filter)
                if 'selected_class' in locals() and selected_class and selected_class != "Todas":
                    groups_query = groups_query.filter(Group.class_name == selected_class)
                # Se admin filtrou docente específico, filtrar disciplina desse docente (já feito acima)
                groups = groups_query.all()
                # Considerar apenas grupos que enviaram submissão
                groups_with_submission = []
                for g in groups:
                    if g.submissions and len(g.submissions) > 0:
                        # Um grupo pode ter apenas uma submissão (nossa lógica), pega a primeira
                        groups_with_submission.append(g)
                if not groups_with_submission:
                    st.write("Nenhum grupo com entregas pendentes para avaliar.")
                else:
                    # Cria mapeamento de status de avaliação
                    evals = session.query(Evaluation).filter(Evaluation.group_id.in_([g.id for g in groups_with_submission])).all()
                    eval_map = {e.group_id: e for e in evals}
                    # Opções para seleção do grupo
                    group_labels = {}
                    for g in groups_with_submission:
                        status = ""
                        if g.id in eval_map:
                            if eval_map[g.id].approved:
                                status = "Aprovado"
                            else:
                                status = "Avaliado"
                        else:
                            status = "Submetido"
                        theme_title = g.theme.title if g.theme else "(tema não definido)"
                        group_labels[g.id] = f"Grupo {g.id} - Tema: {theme_title} - {status}"
                    selected_group_id = st.selectbox("Selecionar Grupo", options=list(group_labels.keys()), format_func=lambda x: group_labels[x])
                    if selected_group_id:
                        # Obter detalhes do grupo selecionado
                        group = session.query(Group).get(selected_group_id)
                        if group:
                            st.write(f"**Tema:** {group.theme.title if group.theme else 'N/D'}")
                            members = [m.name for m in group.members] if group.members else []
                            st.write(f"**Integrantes:** {', '.join(members) if members else 'N/D'}")
                            # Busca avaliação existente (se já avaliado previamente, para editar)
                            evaluation = session.query(Evaluation).filter_by(group_id=group.id).first()
                            # Formulário de avaliação
                            with st.form("evaluation_form", clear_on_submit=False):
                                # Campos de nota para cada critério definido (0 a 10, com passo 0.5)
                                score1 = st.number_input("Nota - Escolha do Tema e Justificativa (Peso 10%)", 0.0, 10.0, value=(evaluation.score1 if evaluation else 0.0), step=0.5)
                                score2 = st.number_input("Nota - Aplicação dos Conceitos do Curso (Peso 30%)", 0.0, 10.0, value=(evaluation.score2 if evaluation else 0.0), step=0.5)
                                score3 = st.number_input("Nota - Análise de Dados e Evidências Empíricas (Peso 20%)", 0.0, 10.0, value=(evaluation.score3 if evaluation else 0.0), step=0.5)
                                score4 = st.number_input("Nota - Qualidade do Relatório Escrito (Peso 15%)", 0.0, 10.0, value=(evaluation.score4 if evaluation else 0.0), step=0.5)
                                score5 = st.number_input("Nota - Qualidade da Apresentação (Slides e Vídeo) (Peso 25%)", 0.0, 10.0, value=(evaluation.score5 if evaluation else 0.0), step=0.5)
                                st.markdown("**Comentários:**")
                                comment_report = st.text_area("Relatório Escrito", value=(evaluation.comment_report if evaluation else ""), height=100)
                                comment_slides = st.text_area("Slides/Apresentação", value=(evaluation.comment_slides if evaluation else ""), height=100)
                                comment_video = st.text_area("Vídeo", value=(evaluation.comment_video if evaluation else ""), height=100)
                                comment_general = st.text_area("Comentário Geral", value=(evaluation.comment_general if evaluation else ""), height=100)
                                approved = st.checkbox("Aprovar este trabalho para a galeria pública", value=(evaluation.approved if evaluation else False))
                                submit_eval = st.form_submit_button("Salvar Avaliação")
                            if submit_eval:
                                if evaluation:
                                    # Atualiza avaliação existente
                                    evaluation.score1 = float(score1)
                                    evaluation.score2 = float(score2)
                                    evaluation.score3 = float(score3)
                                    evaluation.score4 = float(score4)
                                    evaluation.score5 = float(score5)
                                    evaluation.comment_report = comment_report
                                    evaluation.comment_slides = comment_slides
                                    evaluation.comment_video = comment_video
                                    evaluation.comment_general = comment_general
                                    evaluation.approved = bool(approved)
                                    evaluation.eval_timestamp = datetime.datetime.now().isoformat()
                                    session.commit()
                                else:
                                    # Cria nova avaliação
                                    teacher_id = st.session_state.teacher_id if user_role == "teacher" else (st.session_state.teacher_id if "teacher_id" in st.session_state else None)
                                    new_eval = Evaluation(
                                        group_id=group.id,
                                        teacher_id=teacher_id,
                                        score1=float(score1), score2=float(score2), score3=float(score3),
                                        score4=float(score4), score5=float(score5),
                                        comment_report=comment_report, comment_slides=comment_slides,
                                        comment_video=comment_video, comment_general=comment_general,
                                        approved=bool(approved),
                                        eval_timestamp=datetime.datetime.now().isoformat()
                                    )
                                    session.add(new_eval)
                                    session.commit()
                                st.success("Avaliação salva com sucesso.")
                                # Atualiza a lista de status e manutenção do dropdown
                                eval_map[group.id] = evaluation if evaluation else new_eval
                                # (A seleção continuará a mesma, mas o label será atualizado após rerun)
                                st.experimental_rerun()
                session.close()

    # Conteúdo da aba "Relatórios" (somente docentes)
    if tab_relatorios:
        with tab_relatorios:
            st.subheader("📊 Relatórios")
            if user_role not in ["teacher", "admin"]:
                st.error("Acesso restrito aos docentes.")
            else:
                session = SessionLocal()
                # Filtros para relatório
                # Se admin: filtro de docente, disciplina, turma
                if user_role == "admin":
                    teachers = session.query(Teacher).filter(Teacher.discipline != "").all()
                    teacher_options = ["Todos"] + [t.name for t in teachers]
                    selected_teacher = st.selectbox("Docente", teacher_options)
                    teacher_obj = next((t for t in teachers if t.name == selected_teacher), None) if selected_teacher and selected_teacher != "Todos" else None
                    if teacher_obj:
                        # Aplica disciplina do docente e mostra turmas dele
                        disc_filter = teacher_obj.discipline
                        class_options = ["Todas"] + [c[0] for c in session.query(Student.class_name).filter(Student.discipline == disc_filter).distinct().all()]
                    else:
                        disc_options = ["Todas"] + [d[0] for d in session.query(Student.discipline).distinct().all()]
                        disc_filter = st.selectbox("Disciplina", disc_options)
                        class_options = ["Todas"] + [c[0] for c in session.query(Student.class_name).filter((Student.discipline == disc_filter) if disc_filter != "Todas" else True).distinct().all()]
                    selected_class = st.selectbox("Turma", class_options)
                    # Determina disciplina final para usar na query
                    final_disc_filter = teacher_obj.discipline if teacher_obj else (disc_filter if disc_filter != "Todas" else None)
                else:
                    final_disc_filter = st.session_state.discipline
                    class_list = [c[0] for c in session.query(Student.class_name).filter(Student.discipline == final_disc_filter).distinct().all()]
                    if class_list and len(class_list) > 1:
                        class_options = ["Todas"] + class_list
                        selected_class = st.selectbox("Turma", class_options)
                    else:
                        selected_class = class_list[0] if class_list else "Todas"
                        if selected_class != "Todas":
                            st.write(f"Turma: {selected_class}")
                report_type = st.radio("Tipo de Relatório", ["Por Grupo", "Por Aluno", "Por Tema"], horizontal=True)
                generate_btn = st.button("Gerar Relatório")
                if generate_btn:
                    # Geração do relatório conforme tipo escolhido
                    data = []
                    if report_type == "Por Aluno":
                        query = session.query(Student)
                        if final_disc_filter:
                            query = query.filter(Student.discipline == final_disc_filter)
                        if 'selected_class' in locals() and selected_class and selected_class != "Todas":
                            query = query.filter(Student.class_name == selected_class)
                        students = query.all()
                        for s in students:
                            group_id = s.group.id if s.group else None
                            theme = s.group.theme.title if s.group and s.group.theme else ""
                            # Nota final do aluno = nota final do grupo (se avaliado)
                            final_grade = ""
                            status = ""
                            if group_id:
                                eval_obj = session.query(Evaluation).filter(Evaluation.group_id == group_id).first()
                                if eval_obj:
                                    # Calcula nota ponderada final (0-10)
                                    final_score = (eval_obj.score1*0.10 + eval_obj.score2*0.30 + eval_obj.score3*0.20 + eval_obj.score4*0.15 + eval_obj.score5*0.25)
                                    final_grade = f"{final_score:.2f}"
                                    status = "Aprovado" if eval_obj.approved else "Avaliado"
                                else:
                                    # Verifica se submeteu algo
                                    sub = session.query(Submission).filter(Submission.group_id == group_id).first()
                                    status = "Submetido" if sub else "Não submetido"
                            else:
                                status = "Sem grupo"
                            data.append({
                                "RA": s.ra,
                                "Nome": s.name,
                                "Disciplina": s.discipline,
                                "Turma": s.class_name,
                                "Grupo": group_id if group_id else "",
                                "Tema": theme,
                                "Nota Final": final_grade,
                                "Status": status
                            })
                        df = pd.DataFrame(data)
                    elif report_type == "Por Grupo":
                        query = session.query(Group)
                        if final_disc_filter:
                            query = query.filter(Group.discipline == final_disc_filter)
                        if 'selected_class' in locals() and selected_class and selected_class != "Todas":
                            query = query.filter(Group.class_name == selected_class)
                        groups = query.all()
                        for g in groups:
                            members = ", ".join([m.name for m in g.members]) if g.members else ""
                            theme = g.theme.title if g.theme else ""
                            final_grade = ""
                            status = ""
                            eval_obj = session.query(Evaluation).filter(Evaluation.group_id == g.id).first()
                            if eval_obj:
                                final_score = (eval_obj.score1*0.10 + eval_obj.score2*0.30 + eval_obj.score3*0.20 + eval_obj.score4*0.15 + eval_obj.score5*0.25)
                                final_grade = f"{final_score:.2f}"
                                status = "Aprovado" if eval_obj.approved else "Avaliado"
                            else:
                                sub = session.query(Submission).filter(Submission.group_id == g.id).first()
                                status = "Submetido" if sub else "Não submetido"
                            data.append({
                                "Grupo": g.id,
                                "Disciplina": g.discipline,
                                "Turma": g.class_name,
                                "Membros": members,
                                "Tema": theme,
                                "Nota Final": final_grade,
                                "Status": status
                            })
                        df = pd.DataFrame(data)
                    else:  # Por Tema
                        query = session.query(Theme)
                        if final_disc_filter:
                            query = query.filter(Theme.discipline == final_disc_filter)
                        themes = query.all()
                        for t in themes:
                            group = t.group
                            group_id = group.id if group else ""
                            members = ", ".join([m.name for m in group.members]) if group and group.members else ""
                            final_grade = ""
                            status = ""
                            if group:
                                eval_obj = session.query(Evaluation).filter(Evaluation.group_id == group.id).first()
                                if eval_obj:
                                    final_score = (eval_obj.score1*0.10 + eval_obj.score2*0.30 + eval_obj.score3*0.20 + eval_obj.score4*0.15 + eval_obj.score5*0.25)
                                    final_grade = f"{final_score:.2f}"
                                    status = "Aprovado" if eval_obj.approved else "Avaliado"
                                else:
                                    sub = session.query(Submission).filter(Submission.group_id == group.id).first()
                                    status = "Submetido" if sub else "Não submetido"
                            else:
                                status = "Não escolhido"
                            data.append({
                                "Tema": t.title,
                                "Disciplina": t.discipline,
                                "Grupo": group_id,
                                "Integrantes": members,
                                "Nota Final": final_grade,
                                "Status": status
                            })
                        df = pd.DataFrame(data)
                    # Exibe o dataframe
                    st.dataframe(df, use_container_width=True)
                    # Permite download em CSV
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button("⬇️ Baixar CSV", csv, file_name=f"relatorio_{report_type.lower().replace(' ', '_')}.csv", mime="text/csv")
                    # Geração de PDF simples
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_font("Courier", size=10)
                    # Cabeçalho
                    col_names = list(df.columns)
                    # Calcula larguras de coluna aproximadas
                    col_widths = []
                    for col in col_names:
                        max_len = max(len(str(col)), *(len(str(x)) for x in df[col].values))
                        col_widths.append(max_len * 2)  # ajuste de multiplicador para espaçamento
                    header_line = ""
                    for i, col in enumerate(col_names):
                        header_line += str(col).ljust(col_widths[i]+2)  # +2 espaços
                    pdf.cell(0, 5, header_line, ln=1)
                    pdf.cell(0, 5, "-" * len(header_line), ln=1)  # linha separadora
                    # Linhas de dados
                    for index, row in df.iterrows():
                        line = ""
                        for i, col in enumerate(col_names):
                            cell_text = str(row[col]) if str(row[col]) != 'nan' else ""
                            line += cell_text.ljust(col_widths[i]+2)
                        pdf.cell(0, 5, line, ln=1)
                    pdf_bytes = pdf.output(dest='S').encode('latin-1')  # FPDF requer latin-1
                    st.download_button("⬇️ Baixar PDF", data=pdf_bytes, file_name=f"relatorio_{report_type.lower().replace(' ', '_')}.pdf", mime="application/pdf")
                session.close()

    # Conteúdo da aba "Administração" (cadastro de alunos, docentes e temas)
    if tab_admin:
        with tab_admin:
            st.subheader("🔧 Administração do Sistema")
            if user_role not in ["teacher", "admin"]:
                st.error("Acesso restrito aos docentes.")
            else:
                session = SessionLocal()
                admin_tabs = st.tabs(["Alunos", "Docentes", "Temas"])
                tab_alunos, tab_docentes, tab_temas = admin_tabs
                # Gestão de Alunos
                with tab_alunos:
                    st.markdown("**Gerenciar Alunos**")
                    # Filtro por disciplina para exibição
                    disc_options = ["Todas"] + [d[0] for d in session.query(Student.discipline).distinct().all()]
                    selected_disc = st.selectbox("Filtrar Disciplina", disc_options)
                    student_list = session.query(Student).order_by(Student.name).all()
                    if selected_disc and selected_disc != "Todas":
                        student_list = [s for s in student_list if s.discipline == selected_disc]
                    # Monta lista de opções para seleção (RA - Nome)
                    options = ["<Novo Aluno>"] + [f"{s.ra} - {s.name}" for s in student_list]
                    selected_option = st.selectbox("Selecionar Aluno", options)
                    # Preenche campos de acordo com seleção
                    if selected_option == "<Novo Aluno>":
                        sel_student = None
                        default_name = ""
                        default_ra = ""
                        default_email = ""
                        default_disc = selected_disc if selected_disc not in ["", "Todas"] else ""
                        default_class = ""
                    else:
                        ra = selected_option.split(" - ")[0]
                        sel_student = session.query(Student).filter(Student.ra == ra).first()
                        default_name = sel_student.name
                        default_ra = sel_student.ra
                        default_email = sel_student.email if sel_student.email else ""
                        default_disc = sel_student.discipline
                        default_class = sel_student.class_name
                    # Formulário de edição/adição de aluno
                    with st.form("student_form"):
                        name_input = st.text_input("Nome", value=default_name)
                        ra_input = st.text_input("RA", value=default_ra)
                        email_input = st.text_input("Email", value=default_email)
                        # Disciplina e Turma
                        # Se docente logado não admin, fixa disciplina dele para novo aluno (se for adicionar)
                        if user_role == "teacher" and not st.session_state.get("is_admin", False):
                            disc_input = st.text_input("Disciplina", value=st.session_state.discipline, disabled=True)
                            disc_value = st.session_state.discipline
                        else:
                            disc_value = default_disc
                            disc_input = st.text_input("Disciplina", value=disc_value)
                        class_input = st.text_input("Turma", value=default_class)
                        submit_student = st.form_submit_button("Salvar")
                        delete_student = st.form_submit_button("Excluir Aluno", disabled=(sel_student is None))
                    if submit_student:
                        if not name_input or not ra_input or not disc_input or not class_input:
                            st.error("Por favor, preencha Nome, RA, Disciplina e Turma.")
                        else:
                            if sel_student:
                                # Atualiza aluno existente
                                # Verifica se RA foi alterado para algum já existente
                                if ra_input != sel_student.ra:
                                    ra_conflict = session.query(Student).filter(Student.ra == ra_input).first()
                                    if ra_conflict:
                                        st.error("Já existe outro aluno com este RA.")
                                    else:
                                        sel_student.ra = ra_input
                                sel_student.name = name_input
                                sel_student.email = email_input
                                sel_student.discipline = disc_input if disc_input else sel_student.discipline
                                sel_student.class_name = class_input
                                session.commit()
                                st.success("Dados do aluno atualizados.")
                            else:
                                # Novo aluno
                                # Verifica RA duplicado
                                ra_conflict = session.query(Student).filter(Student.ra == ra_input).first()
                                if ra_conflict:
                                    st.error("Já existe um aluno com este RA.")
                                else:
                                    new_student = Student(name=name_input, ra=ra_input, email=email_input,
                                                          discipline=disc_input, class_name=class_input)
                                    session.add(new_student)
                                    session.commit()
                                    st.success("Novo aluno cadastrado.")
                            st.experimental_rerun()
                    if delete_student and sel_student:
                        # Remove aluno (e remove do grupo, se estiver)
                        group = sel_student.group
                        session.delete(sel_student)
                        session.commit()
                        st.success("Aluno excluído.")
                        # Se aluno era único em grupo, podemos optar por remover grupo vazio, mas não faremos automaticamente.
                        st.experimental_rerun()
                # Gestão de Docentes
                with tab_docentes:
                    st.markdown("**Gerenciar Docentes**")
                    if user_role == "teacher" and not st.session_state.get("is_admin", False):
                        st.info("Apenas o administrador pode cadastrar novos docentes. Você pode editar suas informações.")
                    teacher_list = session.query(Teacher).order_by(Teacher.name).all()
                    options = ["<Novo Docente>"] if st.session_state.get("is_admin", False) else []
                    # Lista todos docentes para admin, ou apenas o próprio docente se não admin
                    if st.session_state.get("is_admin", False):
                        options += [f"{t.email} - {t.name}" for t in teacher_list]
                    else:
                        # docente não admin: filtra só ele
                        teacher_list = [t for t in teacher_list if t.email == st.session_state.user_email]
                        options += [f"{t.email} - {t.name}" for t in teacher_list] if teacher_list else []
                    if not options:
                        options = ["<Novo Docente>"]
                    selected_option = st.selectbox("Selecionar Docente", options)
                    if selected_option == "<Novo Docente>":
                        sel_teacher = None
                        default_name = ""
                        default_email = ""
                        default_pin = ""
                        default_disc = ""
                    else:
                        email = selected_option.split(" - ")[0]
                        sel_teacher = session.query(Teacher).filter(Teacher.email == email).first()
                        default_name = sel_teacher.name
                        default_email = sel_teacher.email
                        default_pin = sel_teacher.pin
                        default_disc = sel_teacher.discipline
                    with st.form("teacher_form"):
                        name_input = st.text_input("Nome", value=default_name)
                        email_input = st.text_input("Email", value=default_email, disabled=(sel_teacher is not None and not st.session_state.get("is_admin", False)))
                        pin_input = st.text_input("PIN (senha)", value=default_pin, type="password")
                        disc_input = st.text_input("Disciplina", value=default_disc)
                        submit_teacher = st.form_submit_button("Salvar")
                        delete_teacher = st.form_submit_button("Excluir Docente", disabled=(sel_teacher is None or not st.session_state.get("is_admin", False)))
                    if submit_teacher:
                        if not name_input or not email_input or not pin_input:
                            st.error("Por favor, preencha Nome, Email e PIN.")
                        else:
                            if sel_teacher:
                                # Atualiza docente existente
                                # Se email alterado (não permitido para não-admin)
                                if email_input != sel_teacher.email:
                                    email_conflict = session.query(Teacher).filter(Teacher.email == email_input).first()
                                    if email_conflict:
                                        st.error("Já existe outro docente com este email.")
                                    else:
                                        sel_teacher.email = email_input
                                sel_teacher.name = name_input
                                sel_teacher.pin = pin_input
                                sel_teacher.discipline = disc_input if disc_input else sel_teacher.discipline
                                session.commit()
                                st.success("Dados do docente atualizados.")
                            else:
                                # Novo docente (apenas admin pode adicionar)
                                if not st.session_state.get("is_admin", False):
                                    st.error("Apenas o admin pode adicionar novos docentes.")
                                else:
                                    email_conflict = session.query(Teacher).filter(Teacher.email == email_input).first()
                                    if email_conflict:
                                        st.error("Já existe um docente com este email.")
                                    else:
                                        new_teacher = Teacher(name=name_input, email=email_input, pin=pin_input, discipline=disc_input)
                                        session.add(new_teacher)
                                        session.commit()
                                        st.success("Novo docente cadastrado.")
                            st.experimental_rerun()
                    if delete_teacher and sel_teacher:
                        # Admin exclui docente
                        session.delete(sel_teacher)
                        session.commit()
                        st.success("Docente excluído.")
                        st.experimental_rerun()
                # Gestão de Temas
                with tab_temas:
                    st.markdown("**Gerenciar Temas**")
                    # Filtro de disciplina para temas
                    theme_disciplines = [d[0] for d in session.query(Theme.discipline).distinct().all()]
                    if user_role == "teacher" and not st.session_state.get("is_admin", False):
                        # docente comum só pode ver/adicionar temas da própria disciplina
                        theme_disciplines = [st.session_state.discipline] if st.session_state.discipline else theme_disciplines
                    disc_options = ["Todas"] + theme_disciplines
                    selected_disc = st.selectbox("Filtrar Disciplina", disc_options)
                    theme_list = session.query(Theme).order_by(Theme.title).all()
                    if selected_disc and selected_disc != "Todas":
                        theme_list = [t for t in theme_list if t.discipline == selected_disc]
                    options = ["<Novo Tema>"] + [f"{t.title} ({t.discipline})" for t in theme_list]
                    selected_option = st.selectbox("Selecionar Tema", options)
                    if selected_option == "<Novo Tema>":
                        sel_theme = None
                        default_title = ""
                        default_disc = selected_disc if selected_disc not in ["Todas", ""] else ""
                    else:
                        title = selected_option.split(" (")[0]
                        sel_theme = session.query(Theme).filter(Theme.title == title, Theme.discipline == selected_option.split("(")[1].strip(")")).first()
                        default_title = sel_theme.title
                        default_disc = sel_theme.discipline
                    with st.form("theme_form"):
                        title_input = st.text_input("Título do Tema", value=default_title)
                        # Disciplina do tema: se docente comum, fixar própria
                        if user_role == "teacher" and not st.session_state.get("is_admin", False):
                            disc_input = st.text_input("Disciplina", value=st.session_state.discipline, disabled=True)
                        else:
                            disc_input = st.text_input("Disciplina", value=default_disc)
                        submit_theme = st.form_submit_button("Salvar")
                        delete_theme = st.form_submit_button("Excluir Tema", disabled=(sel_theme is None))
                    if submit_theme:
                        if not title_input or not disc_input:
                            st.error("Por favor, preencha o título e a disciplina do tema.")
                        else:
                            if sel_theme:
                                # Atualiza tema existente
                                # Se título alterado para um já existente na mesma disciplina
                                if title_input != sel_theme.title or disc_input != sel_theme.discipline:
                                    conflict = session.query(Theme).filter(Theme.title == title_input, Theme.discipline == disc_input).first()
                                    if conflict:
                                        st.error("Já existe um tema com este título nessa disciplina.")
                                    else:
                                        # Se tema já estiver vinculado a um grupo e disciplina foi alterada, evitar pois conflitante
                                        if sel_theme.group and sel_theme.discipline != disc_input:
                                            st.error("Não é possível mudar a disciplina de um tema já reservado por um grupo.")
                                        else:
                                            sel_theme.title = title_input
                                            sel_theme.discipline = disc_input
                                            session.commit()
                                            st.success("Tema atualizado.")
                            else:
                                # Novo tema
                                conflict = session.query(Theme).filter(Theme.title == title_input, Theme.discipline == disc_input).first()
                                if conflict:
                                    st.error("Já existe um tema com este título nessa disciplina.")
                                else:
                                    new_theme = Theme(title=title_input, discipline=disc_input)
                                    session.add(new_theme)
                                    session.commit()
                                    st.success("Novo tema cadastrado.")
                            st.experimental_rerun()
                    if delete_theme and sel_theme:
                        # Só permite excluir se tema não estiver associado a nenhum grupo
                        if sel_theme.group:
                            st.error("Não é possível excluir um tema que já foi escolhido por um grupo.")
                        else:
                            session.delete(sel_theme)
                            session.commit()
                            st.success("Tema excluído.")
                            st.experimental_rerun()
                session.close()

    # Conteúdo da aba "Galeria" (pública para alunos e docentes verem trabalhos aprovados)
    if tab_galeria:
        with tab_galeria:
            st.subheader("🌟 Galeria de Trabalhos Aprovados")
            session = SessionLocal()
            approved_evals = session.query(Evaluation).filter(Evaluation.approved == True).all()
            if not approved_evals:
                st.write("Nenhum trabalho foi aprovado para a galeria ainda.")
            else:
                for eval in approved_evals:
                    group = eval.group
                    if not group or not group.theme:
                        continue
                    st.write(f"**Tema:** {group.theme.title}  \n**Grupo {group.id} - {group.discipline} ({group.class_name})**")
                    # Lista integrantes
                    member_names = [m.name for m in group.members] if group.members else []
                    st.write("**Integrantes:** " + ", ".join(member_names))
                    # Links para download dos entregáveis
                    submission = session.query(Submission).filter(Submission.group_id == group.id).first()
                    if submission:
                        files = []
                        if submission.file_report:
                            files.append(("Relatório", submission.file_report))
                        if submission.file_slides:
                            files.append(("Slides", submission.file_slides))
                        if submission.file_video:
                            files.append(("Vídeo", submission.file_video))
                        if files:
                            for label, path in files:
                                # Botão de download se arquivo local ainda existe
                                if os.path.exists(path):
                                    with open(path, "rb") as f:
                                        btn = st.download_button(f"Baixar {label}", data=f, file_name=os.path.basename(path))
                                else:
                                    st.write(f"{label}: (arquivo indisponível no servidor)")
                        st.write("---")
            session.close()
