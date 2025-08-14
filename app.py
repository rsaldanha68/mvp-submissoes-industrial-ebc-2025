import streamlit as st
import sqlite3, os
from datetime import datetime, date
try:
    # Conectar ao banco de dados SQLite (ou criar se não existir)
    conn = sqlite3.connect("submissoes.db")  # use a filename for persistence
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Criar tabelas necessárias, caso não existam
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        name TEXT,
        email TEXT,
        ra TEXT,
        role TEXT,
        pin TEXT,
        authorized INTEGER DEFAULT 0,
        in_ei INTEGER DEFAULT 0,
        in_eb INTEGER DEFAULT 0
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY,
        name TEXT,
        theme_id INTEGER,
        main_class TEXT,
        created_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS group_members (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        group_id INTEGER
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS themes (
        id INTEGER PRIMARY KEY,
        name TEXT,
        description TEXT,
        active INTEGER DEFAULT 1
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY,
        group_id INTEGER,
        report_file TEXT,
        slides_file TEXT,
        materials_file TEXT,
        video_link TEXT,
        report_uploaded INTEGER DEFAULT 0,
        slides_uploaded INTEGER DEFAULT 0,
        materials_uploaded INTEGER DEFAULT 0,
        published INTEGER DEFAULT 0
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY,
        group_id INTEGER,
        teacher_id INTEGER,
        report_score REAL, report_like INTEGER, report_comment_public TEXT, report_comment_private TEXT,
        slides_score REAL, slides_like INTEGER, slides_comment_public TEXT, slides_comment_private TEXT,
        video_score REAL, video_like INTEGER, video_comment_public TEXT, video_comment_private TEXT,
        materials_score REAL, materials_like INTEGER, materials_comment_public TEXT, materials_comment_private TEXT,
        overall_comment_public TEXT, overall_comment_private TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    conn.commit()
    # Criar usuário admin padrão (professor administrador) se não existir
    cur.execute("SELECT * FROM users WHERE role = 'admin'")
    if cur.fetchone() is None:
        cur.execute("INSERT INTO users (name, email, ra, role, pin, authorized, in_ei, in_eb) VALUES (?,?,?,?,?,?,?,?)",
                    ("Admin", "admin", None, "admin", "admin", 1, 1, 1))
        conn.commit()
except Exception as e:
    st.error(f"Erro inicializando o banco de dados: {e}")
    st.stop()

# Função para atualizar o campo main_class do grupo com base na maioria dos membros
def update_group_main_class(group_id: int):
    cur = conn.cursor()
    cur.execute("""SELECT u.in_ei, u.in_eb 
                   FROM group_members gm JOIN users u ON gm.user_id = u.id 
                   WHERE gm.group_id = ?""", (group_id,))
    rows = cur.fetchall()
    count_ei = sum([row["in_ei"] for row in rows])
    count_eb = sum([row["in_eb"] for row in rows])
    if count_ei > count_eb:
        main_class = "EI"  # Economia Industrial
    elif count_eb > count_ei:
        main_class = "EBII"  # Economia Brasileira II
    elif count_ei == count_eb and count_ei != 0:
        main_class = "Misto"
    else:
        main_class = None
    cur.execute("UPDATE groups SET main_class = ? WHERE id = ?", (main_class, group_id))
    conn.commit()
    return main_class

# Função auxiliar para contar membros de um grupo
def get_group_member_count(group_id: int) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM group_members WHERE group_id = ?", (group_id,))
    cnt = cur.fetchone()["cnt"]
    return cnt if cnt is not None else 0

# Configurar integração com SharePoint (credenciais via variáveis de ambiente)
SP_SITE = os.getenv("SHAREPOINT_SITE")       # URL do site SharePoint (ex: https://empresa.sharepoint.com/sites/MeuSite)
SP_FOLDER = os.getenv("SHAREPOINT_FOLDER")   # Caminho da pasta no SharePoint (ex: "/sites/MeuSite/Shared Documents/Submissoes")
SP_USER = os.getenv("SHAREPOINT_USER")       # Usuário (email) para autenticar
SP_PASS = os.getenv("SHAREPOINT_PASS")       # Senha ou PIN do usuário SharePoint
sp_enabled = all([SP_SITE, SP_FOLDER, SP_USER, SP_PASS])

if sp_enabled:
    # Importar biblioteca do SharePoint (Office365-REST-Python-Client)
    try:
        from office365.sharepoint.client_context import ClientContext
        from office365.runtime.auth.authentication_context import AuthenticationContext
        from office365.sharepoint.files.file import File
    except ImportError:
        st.warning("Biblioteca Office365-REST-Python-Client não instalada. Instale para usar integração com SharePoint.")
        sp_enabled = False

def upload_to_sharepoint(file_content: bytes, remote_filename: str) -> bool:
    """Envia um arquivo para o SharePoint. Retorna True se sucesso."""
    try:
        auth = AuthenticationContext(SP_SITE)
        auth.acquire_token_for_user(SP_USER, SP_PASS)
        ctx = ClientContext(SP_SITE, auth)
        # Obtém a pasta alvo no SharePoint
        target_folder = ctx.web.get_folder_by_server_relative_url(SP_FOLDER)
        # Carrega o arquivo na pasta
        target_folder.upload_file(remote_filename, file_content).execute_query()
        return True
    except Exception as e:
        st.error(f"Erro ao enviar '{remote_filename}' para SharePoint: {e}")
        return False

# Iniciar sessão (Streamlit session_state)
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
    st.session_state["user_id"] = None
    st.session_state["role"] = None
    st.session_state["name"] = None

# Interface de login
if not st.session_state["authenticated"]:
    st.title("🔐 Sistema de Submissão de Trabalhos - PUC-SP")
    st.subheader("Faça login para continuar")
    # Escolha de tipo de usuário
    login_type = st.radio("Tipo de Login:", ["Aluno", "Professor/Admin"], index=0)
    if login_type == "Aluno":
        ra_input = st.text_input("RA do Aluno")
    else:
        email_input = st.text_input("E-mail do Professor")
        pin_input = st.text_input("PIN (senha) do Professor", type="password")
    login_btn = st.button("Entrar")
    if login_btn:
        if login_type == "Aluno":
            ra = ra_input.strip()
            cur.execute("SELECT * FROM users WHERE role='student' AND ra = ?", (ra,))
            user = cur.fetchone()
            if user:
                # Login de aluno bem-sucedido
                st.session_state["authenticated"] = True
                st.session_state["user_id"] = user["id"]
                st.session_state["role"] = "student"
                st.session_state["name"] = user["name"]
                st.experimental_rerun()
            else:
                st.error("RA não encontrado. Consulte a administração para cadastro.")
        else:
            email = email_input.strip()
            pin = pin_input.strip()
            cur.execute("SELECT * FROM users WHERE (role='teacher' OR role='admin') AND email = ?", (email,))
            user = cur.fetchone()
            if user and pin and user["pin"] == pin:
                if user["authorized"] == 1 or user["role"] == "admin":
                    # Login de professor/admin bem-sucedido
                    st.session_state["authenticated"] = True
                    st.session_state["user_id"] = user["id"]
                    # Se role for admin no banco, mantemos admin; senão, teacher
                    st.session_state["role"] = "admin" if user["role"] == "admin" else "teacher"
                    st.session_state["name"] = user["name"]
                    st.experimental_rerun()
                else:
                    st.error("Aguardando autorização do administrador para este acesso.")
            else:
                st.error("Credenciais inválidas. Tente novamente.")
    # Acesso público à galeria sem login
    st.markdown("---")
    if st.button("📂 Acessar Galeria Pública"):
        st.session_state["authenticated"] = True
        st.session_state["user_id"] = None
        st.session_state["role"] = "guest"
        st.session_state["name"] = "Visitante"
        st.experimental_rerun()
else:
    # Usuário autenticado (ou guest)
    if st.session_state["role"] == "guest":
        st.sidebar.title("Galeria Pública")
        # Botão para voltar à tela de login
        if st.sidebar.button("Sair da Galeria"):
            # Limpar estado da sessão
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.experimental_rerun()
        # Mostrar somente a galeria pública
        st.header("Galeria de Trabalhos Publicados")
        # Obter todos os grupos (submissões) publicados
        cur.execute("""SELECT g.name AS grupo, t.name AS tema, s.video_link, s.report_file, s.slides_file 
                       FROM groups g JOIN themes t ON g.theme_id = t.id 
                       JOIN submissions s ON s.group_id = g.id 
                       WHERE s.published = 1""")
        published_projects = cur.fetchall()
        if not published_projects:
            st.info("Nenhum trabalho publicado ainda.")
        else:
            for proj in published_projects:
                st.subheader(f"**{proj['tema']}**")
                st.write(f"*Grupo:* {proj['grupo']}")
                # Listar membros do grupo
                cur.execute("""SELECT u.name FROM group_members gm 
                               JOIN users u ON gm.user_id = u.id 
                               WHERE gm.group_id = (SELECT id FROM groups WHERE name = ?)""", (proj["grupo"],))
                members = [row["name"] for row in cur.fetchall()]
                if members:
                    st.write("*Integrantes:* " + ", ".join(members))
                # Mostrar vídeo ou link
                if proj["video_link"]:
                    video_url = proj["video_link"]
                    if "youtube.com" in video_url or "youtu.be" in video_url:
                        st.video(video_url)
                    elif video_url.lower().endswith((".mp4", ".webm", ".ogg")):
                        st.video(video_url)
                    elif video_url.lower().endswith((".mp3", ".wav", ".aac", ".m4a")):
                        st.audio(video_url)
                    else:
                        st.write(f"[👉 Link do Vídeo/Áudio]({video_url})")
                else:
                    st.write("_(Vídeo/áudio não disponível)_")
                st.markdown("---")
        st.stop()  # Não executar mais nada abaixo para guest user

    # Se chegou aqui, usuário é aluno, professor ou admin autenticado
    # Barra lateral de navegação
    menu_options = []
    # Opção de acordo com o papel
    if st.session_state["role"] == "student":
        menu_options = ["Meu Grupo", "Galeria Pública"]
    elif st.session_state["role"] == "teacher":
        menu_options = ["Avaliar Trabalhos", "Galeria Pública"]
    elif st.session_state["role"] == "admin":
        menu_options = ["Avaliar Trabalhos", "Administração", "Galeria Pública"]
    choice = st.sidebar.selectbox("Navegação", menu_options)
    # Botão de logout
    if st.sidebar.button("Logout"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.experimental_rerun()

    # Antes de renderizar páginas: verificar e aplicar regra de bloqueio temporário de temas (grupo <5 antes da data)
    cur.execute("SELECT value FROM config WHERE key = 'min_members_deadline'")
    res = cur.fetchone()
    min_members_deadline = None
    if res:
        try:
            min_members_deadline = datetime.fromisoformat(res["value"]).date()
        except:
            # Se armazenado apenas data "YYYY-MM-DD"
            try:
                min_members_deadline = datetime.strptime(res["value"], "%Y-%m-%d").date()
            except:
                min_members_deadline = None
    if min_members_deadline:
        today = date.today()
        if today > min_members_deadline:
            # Para cada grupo com tema e <5 membros, liberar o tema
            cur.execute("""SELECT g.id, g.theme_id 
                           FROM groups g 
                           WHERE g.theme_id IS NOT NULL""")
            groups_with_theme = cur.fetchall()
            for gr in groups_with_theme:
                gid = gr["id"]
                member_count = get_group_member_count(gid)
                if member_count < 5:
                    # Remover reserva do tema
                    theme_id = gr["theme_id"]
                    cur.execute("UPDATE groups SET theme_id = NULL WHERE id = ?", (gid,))
                    # (Opcional: poderíamos inserir notificação, mas aqui apenas removemos)
            conn.commit()

    # Página: Meu Grupo (Alunos)
    if st.session_state["role"] == "student" and choice == "Meu Grupo":
        st.header("📌 Meu Grupo de Trabalho")
        # Obter dados do aluno logado
        user_id = st.session_state["user_id"]
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        student = cur.fetchone()
        if not student:
            st.error("Usuário não encontrado no sistema.")
        else:
            # Verificar se aluno já pertence a algum grupo
            cur.execute("SELECT group_id FROM group_members WHERE user_id = ?", (user_id,))
            membership = cur.fetchone()
            group_id = membership["group_id"] if membership else None
            if group_id is None:
                st.info("Você ainda não está em um grupo.")
                # Mostrar lista de grupos existentes para entrar
                cur.execute("""SELECT g.id, g.name, g.main_class, COUNT(gm.user_id) as membros
                               FROM groups g LEFT JOIN group_members gm ON g.id = gm.group_id
                               GROUP BY g.id, g.name, g.main_class""")
                groups_list = cur.fetchall()
                if groups_list:
                    st.markdown("**Grupos disponíveis:**")
                    for g in groups_list:
                        gid = g["id"]
                        cur.execute("SELECT theme_id FROM groups WHERE id = ?", (gid,))
                        th = cur.fetchone()
                        theme_taken = th["theme_id"] if th else None
                        # Grupo info
                        class_label = g["main_class"] if g["main_class"] else "N/D"
                        member_count = g["membros"]
                        theme_info = ""
                        if theme_taken:
                            # obter nome do tema
                            cur.execute("SELECT name FROM themes WHERE id = ?", (theme_taken,))
                            tname = cur.fetchone()
                            if tname:
                                theme_info = f"Tema reservado: {tname['name']}"
                        st.write(f"**Grupo {g['name']}** – Turma: {class_label}, Membros: {member_count}. {theme_info}")
                        # Botão para entrar
                        join_key = f"join_{gid}"
                        if not theme_taken or member_count < 5 or (theme_taken and member_count < 5 and min_members_deadline and date.today() <= min_members_deadline):
                            if st.button(f"Entrar no {g['name']}", key=join_key):
                                cur.execute("INSERT INTO group_members (user_id, group_id) VALUES (?,?)", (user_id, gid))
                                conn.commit()
                                update_group_main_class(gid)
                                st.success(f"Você entrou no {g['name']}.")
                                st.experimental_rerun()
                        else:
                            # Se o grupo já está cheio ou com tema confirmado, não permitir entrar
                            st.button(f"Entrar no {g['name']}", key=join_key, disabled=True)
                # Formulário para criar novo grupo
                st.markdown("**Criar novo grupo:**")
                new_group_name = st.text_input("Nome do novo grupo (opcional, gerado automaticamente se vazio):")
                create_btn = st.button("Criar Grupo")
                if create_btn:
                    name_val = new_group_name.strip() if new_group_name.strip() else None
                    # Se não fornecido nome, definir como "Grupo <N>"
                    if not name_val:
                        # Gerar nome automático
                        cur.execute("SELECT MAX(id) as maxid FROM groups")
                        maxid = cur.fetchone()["maxid"]
                        next_num = (maxid + 1) if maxid else 1
                        name_val = f"Grupo {next_num}"
                    cur.execute("INSERT INTO groups (name, created_at) VALUES (?,?)",
                                (name_val, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    gid = cur.lastrowid
                    # Adicionar o criador como membro
                    cur.execute("INSERT INTO group_members (user_id, group_id) VALUES (?,?)", (user_id, gid))
                    conn.commit()
                    update_group_main_class(gid)
                    st.success(f"Grupo '{name_val}' criado e você foi adicionado a ele.")
                    st.experimental_rerun()
            else:
                # Aluno já está em um grupo
                cur.execute("SELECT * FROM groups WHERE id = ?", (group_id,))
                group = cur.fetchone()
                st.write(f"**Grupo atual:** {group['name']} – Turma principal: {group['main_class'] if group['main_class'] else 'N/D'}")
                # Listar membros do grupo
                cur.execute("""SELECT u.name, u.ra FROM group_members gm 
                               JOIN users u ON gm.user_id = u.id 
                               WHERE gm.group_id = ?""", (group_id,))
                members = cur.fetchall()
                member_names = [f"{m['name']} (RA {m['ra']})" if m['ra'] else m['name'] for m in members]
                st.write("**Membros do grupo:** " + ", ".join(member_names))
                # Permitir saída do grupo
                if st.button("Sair do grupo"):
                    cur.execute("DELETE FROM group_members WHERE user_id = ? AND group_id = ?", (user_id, group_id))
                    conn.commit()
                    # Se grupo ficar vazio, remover grupo também
                    if get_group_member_count(group_id) == 0:
                        cur.execute("DELETE FROM groups WHERE id = ?", (group_id,))
                        # Liberar tema se tinha
                        # (Como apagamos o grupo, o tema automaticamente fica livre porque group.theme_id não existirá)
                        conn.commit()
                    else:
                        # Se grupo ainda tem membros, atualizar main_class
                        update_group_main_class(group_id)
                    st.session_state["reload"] = True  # sinalizar para recarregar
                    st.success("Você saiu do grupo.")
                    st.experimental_rerun()
                # Se grupo possui tema definido
                theme_name = None
                if group["theme_id"]:
                    cur.execute("SELECT name FROM themes WHERE id = ?", (group["theme_id"],))
                    trow = cur.fetchone()
                    if trow:
                        theme_name = trow["name"]
                # Se não tem tema, permitir escolher
                if not theme_name:
                    st.warning("Este grupo ainda não reservou um tema.")
                    # Listar temas disponíveis
                    cur.execute("""SELECT id, name FROM themes 
                                   WHERE active = 1 AND id NOT IN 
                                         (SELECT theme_id FROM groups WHERE theme_id IS NOT NULL)""")
                    themes_available = cur.fetchall()
                    if themes_available:
                        theme_options = [t["name"] for t in themes_available]
                        chosen_theme = st.selectbox("Escolha um tema para reservar:", ["(Selecionar tema)"] + theme_options)
                        if chosen_theme and chosen_theme != "(Selecionar tema)":
                            theme_obj = next((t for t in themes_available if t["name"] == chosen_theme), None)
                            if theme_obj:
                                if st.button("Reservar Tema"):
                                    cur.execute("UPDATE groups SET theme_id = ? WHERE id = ?", (theme_obj["id"], group_id))
                                    conn.commit()
                                    st.success(f"Tema '{chosen_theme}' reservado para o grupo.")
                                    st.experimental_rerun()
                    else:
                        st.info("Não há temas disponíveis no momento.")
                else:
                    st.info(f"**Tema do grupo:** {theme_name}")
                    # Regra: se grupo <5 membros antes da data limite, a reserva é temporária
                    if min_members_deadline:
                        today = date.today()
                        member_count = get_group_member_count(group_id)
                        if today <= min_members_deadline and member_count < 5:
                            st.warning(f"A reserva do tema é temporária. O grupo precisa ter pelo menos 5 membros até {min_members_deadline.strftime('%d/%m/%Y')} para manter o tema.")
                        elif today > min_members_deadline and member_count < 5:
                            st.error(f"O grupo não atingiu 5 membros até {min_members_deadline.strftime('%d/%m/%Y')}. A reserva do tema pode ter sido cancelada pelo sistema.")
                st.markdown("---")
                # Upload de entregáveis
                st.subheader("📎 Envio de Entregáveis")
                # Verificar se prazo de entrega definido e se já passou
                submission_deadline = None
                cur.execute("SELECT value FROM config WHERE key = 'submission_deadline'")
                res = cur.fetchone()
                if res:
                    try:
                        submission_deadline = datetime.fromisoformat(res["value"]).date()
                    except:
                        try:
                            submission_deadline = datetime.strptime(res["value"], "%Y-%m-%d").date()
                        except:
                            submission_deadline = None
                if submission_deadline and date.today() > submission_deadline:
                    st.error(f"O prazo de submissão se encerrou em {submission_deadline.strftime('%d/%m/%Y')}. Não é possível enviar novos arquivos.")
                else:
                    # Buscar registro de submissão (ou criar se não existe)
                    cur.execute("SELECT * FROM submissions WHERE group_id = ?", (group_id,))
                    submission = cur.fetchone()
                    if not submission:
                        # Criar registro vazio de submissão para este grupo
                        cur.execute("INSERT INTO submissions (group_id) VALUES (?)", (group_id,))
                        conn.commit()
                        cur.execute("SELECT * FROM submissions WHERE group_id = ?", (group_id,))
                        submission = cur.fetchone()
                    # Mostrar status atual dos arquivos enviados
                    file_status = []
                    if submission["report_file"]:
                        file_status.append(f"Relatório: **{submission['report_file']}**")
                    if submission["slides_file"]:
                        file_status.append(f"Slides: **{submission['slides_file']}**")
                    if submission["materials_file"]:
                        file_status.append(f"Materiais: **{submission['materials_file']}**")
                    if submission["video_link"]:
                        file_status.append(f"Link de Vídeo/Áudio: {submission['video_link']}")
                    if file_status:
                        st.write("Entregáveis já enviados: " + "; ".join(file_status))
                    # Formulário de upload
                    with st.form(key="upload_form"):
                        report_file = st.file_uploader("Relatório (PDF)", type=["pdf"], key="report_upl")
                        slides_file = st.file_uploader("Apresentação (PPTX ou PDF)", type=["pptx", "pdf"], key="slides_upl")
                        materials_file = st.file_uploader("Materiais Suporte (ZIP)", type=["zip"], key="mat_upl")
                        video_link_input = st.text_input("Link para Vídeo/Áudio (URL)", key="video_link")
                        upload_submit = st.form_submit_button("Enviar Entregáveis")
                        if upload_submit:
                            # Processar cada arquivo enviado
                            updated_fields = []
                            if report_file:
                                # Salvar o arquivo PDF do relatório localmente
                                report_filename = f"grupo{group_id}_relatorio.pdf"
                                with open(report_filename, "wb") as f:
                                    f.write(report_file.getbuffer())
                                updated_fields.append("Relatório")
                                # Atualizar no banco
                                cur.execute("UPDATE submissions SET report_file = ?, report_uploaded = ? WHERE group_id = ?",
                                            (report_filename, 0, group_id))
                                conn.commit()
                                # Upload para SharePoint, se habilitado
                                if sp_enabled:
                                    success = upload_to_sharepoint(report_file.getbuffer(), report_filename)
                                    if success:
                                        cur.execute("UPDATE submissions SET report_uploaded = 1 WHERE group_id = ?", (group_id,))
                                        conn.commit()
                            if slides_file:
                                slides_filename = f"grupo{group_id}_slides.{slides_file.name.split('.')[-1]}"
                                with open(slides_filename, "wb") as f:
                                    f.write(slides_file.getbuffer())
                                updated_fields.append("Slides")
                                cur.execute("UPDATE submissions SET slides_file = ?, slides_uploaded = ? WHERE group_id = ?",
                                            (slides_filename, 0, group_id))
                                conn.commit()
                                if sp_enabled:
                                    success = upload_to_sharepoint(slides_file.getbuffer(), slides_filename)
                                    if success:
                                        cur.execute("UPDATE submissions SET slides_uploaded = 1 WHERE group_id = ?", (group_id,))
                                        conn.commit()
                            if materials_file:
                                materials_filename = f"grupo{group_id}_materiais.zip"
                                with open(materials_filename, "wb") as f:
                                    f.write(materials_file.getbuffer())
                                updated_fields.append("Materiais")
                                cur.execute("UPDATE submissions SET materials_file = ?, materials_uploaded = ? WHERE group_id = ?",
                                            (materials_filename, 0, group_id))
                                conn.commit()
                                if sp_enabled:
                                    success = upload_to_sharepoint(materials_file.getbuffer(), materials_filename)
                                    if success:
                                        cur.execute("UPDATE submissions SET materials_uploaded = 1 WHERE group_id = ?", (group_id,))
                                        conn.commit()
                            if video_link_input:
                                cur.execute("UPDATE submissions SET video_link = ? WHERE group_id = ?", (video_link_input.strip(), group_id))
                                conn.commit()
                                updated_fields.append("Link de Vídeo/Áudio")
                            if updated_fields:
                                st.success(f"Entregável(s) atualizado(s): {', '.join(updated_fields)}")
                                st.experimental_rerun()
                            else:
                                st.warning("Nenhum arquivo/link foi fornecido.")
                # Exibir feedback dos professores (comentários públicos e notas, se disponíveis)
                st.markdown("---")
                st.subheader("📊 Feedback dos Professores")
                cur.execute("""SELECT e.*, u.name as teacher_name 
                               FROM evaluations e JOIN users u ON e.teacher_id = u.id 
                               WHERE e.group_id = ?""", (group_id,))
                evals = cur.fetchall()
                if not evals:
                    st.info("Nenhuma avaliação disponível no momento.")
                else:
                    for ev in evals:
                        teacher_name = ev["teacher_name"]
                        st.markdown(f"**Avaliação de {teacher_name}:**")
                        # Exibir notas e comentários públicos por entregável
                        if ev["report_score"] is not None or ev["report_comment_public"]:
                            st.write(f"**Relatório:** Nota = {ev['report_score'] if ev['report_score'] is not None else '-'}; 👍 = {'Sim' if ev['report_like'] else 'Não'}")
                            if ev["report_comment_public"]:
                                st.write(f"*Comentário:* {ev['report_comment_public']}")
                        if ev["slides_score"] is not None or ev["slides_comment_public"]:
                            st.write(f"**Slides:** Nota = {ev['slides_score'] if ev['slides_score'] is not None else '-'}; 👍 = {'Sim' if ev['slides_like'] else 'Não'}")
                            if ev["slides_comment_public"]:
                                st.write(f"*Comentário:* {ev['slides_comment_public']}")
                        if ev["video_score"] is not None or ev["video_comment_public"]:
                            st.write(f"**Vídeo:** Nota = {ev['video_score'] if ev['video_score'] is not None else '-'}; 👍 = {'Sim' if ev['video_like'] else 'Não'}")
                            if ev["video_comment_public"]:
                                st.write(f"*Comentário:* {ev['video_comment_public']}")
                        if ev["materials_score"] is not None or ev["materials_comment_public"]:
                            st.write(f"**Materiais:** Nota = {ev['materials_score'] if ev['materials_score'] is not None else '-'}; 👍 = {'Sim' if ev['materials_like'] else 'Não'}")
                            if ev["materials_comment_public"]:
                                st.write(f"*Comentário:* {ev['materials_comment_public']}")
                        if ev["overall_comment_public"]:
                            st.write(f"**Comentário Geral:** {ev['overall_comment_public']}")
                        st.markdown("---")

    # Página: Avaliar Trabalhos (Professores e Admin)
    if (st.session_state["role"] in ["teacher", "admin"]) and choice == "Avaliar Trabalhos":
        st.header("📝 Avaliação de Trabalhos")
        # Selecionar grupo para avaliar
        # Filtrar grupos relevantes para o professor, baseado nas turmas que ele leciona
        teacher_id = st.session_state["user_id"]
        # Determinar cursos do professor
        cur.execute("SELECT in_ei, in_eb FROM users WHERE id = ?", (teacher_id,))
        teacher = cur.fetchone()
        teacher_courses = []
        if teacher["in_ei"]:
            teacher_courses.append("EI")
        if teacher["in_eb"]:
            teacher_courses.append("EBII")
        # Buscar grupos para avaliar:
        if teacher_courses and "EI" in teacher_courses and "EBII" in teacher_courses:
            # Professor ligado a ambos cursos (ou admin): vê todos os grupos
            cur.execute("""SELECT g.id, g.name, t.name as tema 
                           FROM groups g LEFT JOIN themes t ON g.theme_id = t.id 
                           WHERE g.theme_id IS NOT NULL""")
        elif teacher_courses and "EI" in teacher_courses:
            # Apenas EI: grupos que tenham pelo menos um membro de Economia Industrial
            cur.execute("""SELECT DISTINCT g.id, g.name, t.name as tema 
                           FROM groups g 
                           JOIN group_members gm ON g.id = gm.group_id
                           JOIN users u ON gm.user_id = u.id
                           LEFT JOIN themes t ON g.theme_id = t.id
                           WHERE u.in_ei = 1 AND g.theme_id IS NOT NULL""")
        elif teacher_courses and "EBII" in teacher_courses:
            cur.execute("""SELECT DISTINCT g.id, g.name, t.name as tema 
                           FROM groups g 
                           JOIN group_members gm ON g.id = gm.group_id
                           JOIN users u ON gm.user_id = u.id
                           LEFT JOIN themes t ON g.theme_id = t.id
                           WHERE u.in_eb = 1 AND g.theme_id IS NOT NULL""")
        else:
            # Professor sem curso associado (teoricamente não ocorre)
            cur.execute("SELECT g.id, g.name, t.name as tema FROM groups g LEFT JOIN themes t ON g.theme_id = t.id WHERE g.theme_id IS NOT NULL")
        groups_to_eval = cur.fetchall()
        if not groups_to_eval:
            st.info("Não há grupos com temas reservados para avaliar ainda.")
        else:
            # Dropdown de grupos com nome e tema
            options = [f"Grupo {g['name']} - Tema: {g['tema']}" for g in groups_to_eval]
            selected = st.selectbox("Selecione um grupo para avaliar:", ["(Selecionar)"] + options)
            if selected and selected != "(Selecionar)":
                # Identificar o grupo selecionado
                idx = options.index(selected)
                group_data = groups_to_eval[idx]
                sel_group_id = group_data["id"]
                sel_group_name = group_data["name"]
                sel_group_theme = group_data["tema"]
                st.subheader(f"Avaliando {sel_group_name} – Tema: {sel_group_theme}")
                # Mostrar links para baixar entregáveis (se disponíveis)
                cur.execute("SELECT * FROM submissions WHERE group_id = ?", (sel_group_id,))
                submission = cur.fetchone()
                if submission:
                    # Disponibilizar arquivos para download (se local)
                    if submission["report_file"]:
                        try:
                            with open(submission["report_file"], "rb") as f:
                                st.download_button("📥 Baixar Relatório", f, file_name=submission["report_file"])
                        except FileNotFoundError:
                            st.write("Relatório enviado (armazenado externamente).")
                    if submission["slides_file"]:
                        try:
                            with open(submission["slides_file"], "rb") as f:
                                st.download_button("📥 Baixar Slides", f, file_name=submission["slides_file"])
                        except FileNotFoundError:
                            st.write("Slides enviados (armazenados externamente).")
                    if submission["materials_file"]:
                        try:
                            with open(submission["materials_file"], "rb") as f:
                                st.download_button("📥 Baixar Materiais", f, file_name=submission["materials_file"])
                        except FileNotFoundError:
                            st.write("Materiais de apoio enviados (armazenados externamente).")
                    if submission["video_link"]:
                        st.write(f"💻 **Link do Vídeo/Áudio:** {submission['video_link']}")
                else:
                    st.write("*(Nenhuma entrega enviada por este grupo ainda.)*")
                st.markdown("---")
                # Verificar se já existe avaliação deste professor para este grupo
                cur.execute("SELECT * FROM evaluations WHERE group_id = ? AND teacher_id = ?", (sel_group_id, teacher_id))
                existing_eval = cur.fetchone()
                # Preparar formulário de avaliação
                with st.form(key=f"eval_form_{sel_group_id}_{teacher_id}"):
                    st.markdown("**Avaliação do Relatório:**")
                    rep_score = st.number_input("Nota (Relatório)", min_value=0.0, max_value=10.0, step=0.5,
                                                value=(existing_eval["report_score"] if existing_eval and existing_eval["report_score"] is not None else 0.0))
                    rep_like = st.checkbox("Curtir Relatório", value=(existing_eval["report_like"] == 1 if existing_eval else False))
                    rep_comment_pub = st.text_area("Comentário Público - Relatório", 
                                                   value=(existing_eval["report_comment_public"] if existing_eval else ""))
                    rep_comment_priv = st.text_area("Comentário Privado - Relatório", 
                                                    value=(existing_eval["report_comment_private"] if existing_eval else ""))
                    st.markdown("**Avaliação dos Slides:**")
                    sli_score = st.number_input("Nota (Slides)", min_value=0.0, max_value=10.0, step=0.5,
                                                value=(existing_eval["slides_score"] if existing_eval and existing_eval["slides_score"] is not None else 0.0))
                    sli_like = st.checkbox("Curtir Slides", value=(existing_eval["slides_like"] == 1 if existing_eval else False))
                    sli_comment_pub = st.text_area("Comentário Público - Slides", 
                                                   value=(existing_eval["slides_comment_public"] if existing_eval else ""))
                    sli_comment_priv = st.text_area("Comentário Privado - Slides", 
                                                    value=(existing_eval["slides_comment_private"] if existing_eval else ""))
                    st.markdown("**Avaliação do Vídeo:**")
                    vid_score = st.number_input("Nota (Vídeo)", min_value=0.0, max_value=10.0, step=0.5,
                                                value=(existing_eval["video_score"] if existing_eval and existing_eval["video_score"] is not None else 0.0))
                    vid_like = st.checkbox("Curtir Vídeo", value=(existing_eval["video_like"] == 1 if existing_eval else False))
                    vid_comment_pub = st.text_area("Comentário Público - Vídeo", 
                                                   value=(existing_eval["video_comment_public"] if existing_eval else ""))
                    vid_comment_priv = st.text_area("Comentário Privado - Vídeo", 
                                                    value=(existing_eval["video_comment_private"] if existing_eval else ""))
                    st.markdown("**Avaliação dos Materiais:**")
                    mat_score = st.number_input("Nota (Materiais)", min_value=0.0, max_value=10.0, step=0.5,
                                                value=(existing_eval["materials_score"] if existing_eval and existing_eval["materials_score"] is not None else 0.0))
                    mat_like = st.checkbox("Curtir Materiais", value=(existing_eval["materials_like"] == 1 if existing_eval else False))
                    mat_comment_pub = st.text_area("Comentário Público - Materiais", 
                                                   value=(existing_eval["materials_comment_public"] if existing_eval else ""))
                    mat_comment_priv = st.text_area("Comentário Privado - Materiais", 
                                                    value=(existing_eval["materials_comment_private"] if existing_eval else ""))
                    st.markdown("**Comentário Geral:**")
                    overall_pub = st.text_area("Comentário Público Geral", value=(existing_eval["overall_comment_public"] if existing_eval else ""))
                    overall_priv = st.text_area("Comentário Privado Geral", value=(existing_eval["overall_comment_private"] if existing_eval else ""))
                    submit_eval = st.form_submit_button("Salvar Avaliação")
                    if submit_eval:
                        if existing_eval:
                            # Atualizar avaliação existente
                            cur.execute("""UPDATE evaluations SET 
                                           report_score=?, report_like=?, report_comment_public=?, report_comment_private=?,
                                           slides_score=?, slides_like=?, slides_comment_public=?, slides_comment_private=?,
                                           video_score=?, video_like=?, video_comment_public=?, video_comment_private=?,
                                           materials_score=?, materials_like=?, materials_comment_public=?, materials_comment_private=?,
                                           overall_comment_public=?, overall_comment_private=?
                                           WHERE id=?""",
                                        (rep_score, 1 if rep_like else 0, rep_comment_pub, rep_comment_priv,
                                         sli_score, 1 if sli_like else 0, sli_comment_pub, sli_comment_priv,
                                         vid_score, 1 if vid_like else 0, vid_comment_pub, vid_comment_priv,
                                         mat_score, 1 if mat_like else 0, mat_comment_pub, mat_comment_priv,
                                         overall_pub, overall_priv, existing_eval["id"]))
                        else:
                            # Inserir nova avaliação
                            cur.execute("""INSERT INTO evaluations (
                                           group_id, teacher_id,
                                           report_score, report_like, report_comment_public, report_comment_private,
                                           slides_score, slides_like, slides_comment_public, slides_comment_private,
                                           video_score, video_like, video_comment_public, video_comment_private,
                                           materials_score, materials_like, materials_comment_public, materials_comment_private,
                                           overall_comment_public, overall_comment_private)
                                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                        (sel_group_id, teacher_id,
                                         rep_score, 1 if rep_like else 0, rep_comment_pub, rep_comment_priv,
                                         sli_score, 1 if sli_like else 0, sli_comment_pub, sli_comment_priv,
                                         vid_score, 1 if vid_like else 0, vid_comment_pub, vid_comment_priv,
                                         mat_score, 1 if mat_like else 0, mat_comment_pub, mat_comment_priv,
                                         overall_pub, overall_priv))
                        conn.commit()
                        st.success("Avaliação salva com sucesso!")
                        # Opcional: após salvar, poderia limpar seleção ou manter
                        st.experimental_rerun()

    # Página: Administração (somente Admin)
    if st.session_state["role"] == "admin" and choice == "Administração":
        st.header("⚙️ Administração do Sistema")
        st.subheader("Cadastro de Alunos e Professores")
        col1, col2 = st.columns(2)
        # Formulário para adicionar aluno
        with col1:
            st.markdown("**Adicionar Aluno:**")
            stud_name = st.text_input("Nome do Aluno")
            stud_ra = st.text_input("RA do Aluno")
            stud_ei = st.checkbox("Matriculado em Economia Industrial")
            stud_eb = st.checkbox("Matriculado em Economia Brasileira II")
            add_stud = st.button("Cadastrar Aluno")
            if add_stud:
                if stud_name.strip() == "" or stud_ra.strip() == "":
                    st.warning("Nome e RA são obrigatórios.")
                else:
                    # Verificar duplicata de RA
                    cur.execute("SELECT * FROM users WHERE role='student' AND ra = ?", (stud_ra.strip(),))
                    if cur.fetchone():
                        st.error("Já existe um aluno cadastrado com este RA.")
                    else:
                        cur.execute("INSERT INTO users (name, email, ra, role, pin, authorized, in_ei, in_eb) VALUES (?,?,?,?,?,?,?,?)",
                                    (stud_name.strip(), None, stud_ra.strip(), "student", None, 1, 1 if stud_ei else 0, 1 if stud_eb else 0))
                        conn.commit()
                        st.success("Aluno cadastrado com sucesso!")
        # Formulário para adicionar professor
        with col2:
            st.markdown("**Adicionar Professor:**")
            prof_name = st.text_input("Nome do Professor")
            prof_email = st.text_input("E-mail do Professor")
            prof_pin = st.text_input("PIN/Senha Inicial")
            prof_ei = st.checkbox("Leciona Economia Industrial", key="pe")
            prof_eb = st.checkbox("Leciona Economia Brasileira II", key="pb")
            prof_admin = st.checkbox("Conceder acesso de Administrador")
            add_prof = st.button("Cadastrar Professor")
            if add_prof:
                if prof_name.strip() == "" or prof_email.strip() == "" or prof_pin.strip() == "":
                    st.warning("Nome, e-mail e PIN são obrigatórios.")
                else:
                    cur.execute("SELECT * FROM users WHERE email = ?", (prof_email.strip(),))
                    if cur.fetchone():
                        st.error("Já existe um usuário cadastrado com este e-mail.")
                    else:
                        role_val = "admin" if prof_admin else "teacher"
                        auth_val = 1 if role_val == "admin" else 0  # professor comum inicia aguardando autorização
                        cur.execute("INSERT INTO users (name, email, ra, role, pin, authorized, in_ei, in_eb) VALUES (?,?,?,?,?,?,?,?)",
                                    (prof_name.strip(), prof_email.strip(), None, role_val, prof_pin.strip(), auth_val, 1 if prof_ei else 0, 1 if prof_eb else 0))
                        conn.commit()
                        st.success("Professor cadastrado com sucesso! (necessário autorização pelo admin, exceto para admin)")
        st.markdown("---")
        # Listar e gerenciar professores existentes
        st.subheader("Gerenciar Professores")
        cur.execute("SELECT * FROM users WHERE role='teacher' OR role='admin'")
        teachers = cur.fetchall()
        if teachers:
            st.markdown("Marque/desmarque para autorizar acesso e permissões de admin:")
            form = st.form("teacher_manage_form")
            # Tabela básica de professores com checkboxes
            for t in teachers:
                tid = t["id"]
                is_auth = True if t["authorized"] == 1 or t["role"] == "admin" else False
                is_admin = True if t["role"] == "admin" else False
                auth_cb = form.checkbox(f"{t['name']} ({t['email']}) autorizado", value=is_auth, key=f"auth_{tid}")
                admin_cb = form.checkbox(f"{t['name']} - admin", value=is_admin, key=f"adm_{tid}")
            submitted = form.form_submit_button("Atualizar Professores")
            if submitted:
                # Atualizar todos conforme marcado
                for t in teachers:
                    tid = t["id"]
                    new_auth = 1 if st.session_state.get(f"auth_{tid}") else 0
                    new_admin = st.session_state.get(f"adm_{tid}")
                    # Se for admin marcado e não era, atualizar role
                    # Se admin desmarcado e era admin, rebaixar para teacher
                    new_role = t["role"]
                    if new_admin and t["role"] != "admin":
                        new_role = "admin"
                        new_auth = 1  # admin sempre autorizado
                    elif not new_admin and t["role"] == "admin":
                        new_role = "teacher"
                        # Nota: se removido admin, mantemos authorized como estava (provavelmente sim, queremos que continue autorizado como teacher)
                    # Atualizar DB
                    cur.execute("UPDATE users SET role=?, authorized=? WHERE id=?", (new_role, new_auth, tid))
                conn.commit()
                st.success("Informações de professores atualizadas.")
        else:
            st.write("Não há professores cadastrados.")
        st.markdown("---")
        # Listar e gerenciar alunos existentes
        st.subheader("Gerenciar Alunos")
        cur.execute("SELECT * FROM users WHERE role='student'")
        students = cur.fetchall()
        if students:
            st.markdown("Lista de alunos cadastrados:")
            for s in students:
                courses = []
                if s["in_ei"]:
                    courses.append("EI")
                if s["in_eb"]:
                    courses.append("EBII")
                flag = ""
                if (s["in_ei"] and not s["in_eb"]) or (s["in_eb"] and not s["in_ei"]):
                    flag = " *(apenas 1 disciplina)*"
                st.write(f"{s['name']} – RA {s['ra']} – Disciplinas: {', '.join(courses)}{flag}")
        else:
            st.write("Não há alunos cadastrados.")
        st.markdown("---")
        # Gerenciar Temas
        st.subheader("Temas dos Trabalhos")
        # Adicionar novo tema
        with st.form(key="add_theme_form"):
            new_theme_name = st.text_input("Título do novo Tema")
            new_theme_desc = st.text_area("Descrição do Tema (opcional)")
            add_theme = st.form_submit_button("Adicionar Tema")
            if add_theme:
                if new_theme_name.strip() == "":
                    st.warning("Título do tema é obrigatório.")
                else:
                    cur.execute("INSERT INTO themes (name, description, active) VALUES (?,?,?)",
                                (new_theme_name.strip(), new_theme_desc.strip(), 1))
                    conn.commit()
                    st.success("Tema adicionado com sucesso.")
        # Listar temas existentes
        cur.execute("SELECT t.id, t.name, t.description, g.name as grupo 
                    FROM themes t LEFT JOIN groups g ON t.id = g.theme_id""")
        themes_list = cur.fetchall()
