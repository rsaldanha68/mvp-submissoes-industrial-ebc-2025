# app.py
import streamlit as st
from datetime import datetime
import os, json, urllib
import requests

# PDF opcional (relatórios). Se não tiver fpdf, o app continua, só desativa PDF.
try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

# ----------------------------
# Banco via SQLAlchemy (SQLite)
# ----------------------------
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, ForeignKey, Text
)
from sqlalchemy.orm import sessionmaker, relationship, declarative_base

DB_PATH = os.path.join(os.getcwd(), "submissions_app.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
Base = declarative_base()

# ---------- MODELOS ----------
class Discipline(Base):
    __tablename__ = 'disciplines'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    offerings = relationship("Offering", back_populates="discipline")

class Teacher(Base):
    __tablename__ = 'teachers'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)     # armazenado em minúsculas
    password = Column(String, nullable=False)               # (em produção, usar hash)
    offerings = relationship("Offering", back_populates="teacher")

class Offering(Base):
    __tablename__ = 'offerings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    discipline_id = Column(Integer, ForeignKey('disciplines.id'), nullable=False)
    name = Column(String, nullable=False)      # ex.: "MA6", "MB6", "NA6", "NB6"
    teacher_id = Column(Integer, ForeignKey('teachers.id'))
    discipline = relationship("Discipline", back_populates="offerings")
    teacher = relationship("Teacher", back_populates="offerings")

class Student(Base):
    __tablename__ = 'students'
    id = Column(Integer, primary_key=True, autoincrement=True)
    ra = Column(String)  # RA
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)     # armazenado em minúsculas
    password = Column(String, nullable=False)               # (em produção, usar hash)
    industrial_class_id = Column(Integer, ForeignKey('offerings.id'))
    ebc_class_id = Column(Integer, ForeignKey('offerings.id'))
    industrial_class = relationship("Offering", foreign_keys=[industrial_class_id])
    ebc_class = relationship("Offering", foreign_keys=[ebc_class_id])
    groups_assoc = relationship("GroupMember", back_populates="student")
    groups = relationship("Group", secondary="group_members", back_populates="members")

class Group(Base):
    __tablename__ = 'groups'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)      # "Grupo MA6-1" etc.
    theme = Column(String, nullable=False)     # Sem duplicidade (app garante)
    primary_offering_id = Column(Integer, ForeignKey('offerings.id'))
    primary_offering = relationship("Offering")
    # Avaliação (por UC)
    industrial_grade = Column(String)
    ebc_grade = Column(String)
    industrial_comment = Column(Text)
    ebc_comment = Column(Text)
    industrial_approved = Column(Boolean, default=False)
    ebc_approved = Column(Boolean, default=False)
    members_assoc = relationship("GroupMember", back_populates="group")
    members = relationship("Student", secondary="group_members", back_populates="groups")

class GroupMember(Base):
    __tablename__ = 'group_members'
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('groups.id'), nullable=False)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False)
    participation = Column(Text)
    group = relationship("Group", back_populates="members_assoc")
    student = relationship("Student", back_populates="groups_assoc")

class Submission(Base):
    __tablename__ = 'submissions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('groups.id'), nullable=False)
    timestamp = Column(String, nullable=False)  # ex.: "20250817_103501"
    files = Column(Text, nullable=False)        # JSON: {"termo":"...", "relatorio":"...", "slides":"...", "video":"...", "_meta":{...}}
    group = relationship("Group")

Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

# ----------------------------
# Seeds mínimos (disciplinas)
# ----------------------------
if session.query(Discipline).count() == 0:
    session.add_all([
        Discipline(name="Economia Industrial"),
        Discipline(name="Economia Brasileira Contemporânea II")
    ])
    session.commit()

disc_ind = session.query(Discipline).filter(Discipline.name.like("%Industrial%")).first()
disc_ebc = session.query(Discipline).filter(Discipline.name.like("%Brasileira%")).first()

# ------------------------------------------------
# Admin de testes + pulo de login (via secrets)
# ------------------------------------------------
ADMIN_EMAIL = (st.secrets.get("ADMIN_EMAIL", "rsaldanha@pucsp.br") or "").lower()
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "8722")
DEV_QUICK_LOGIN = bool(st.secrets.get("DEV_QUICK_LOGIN", False))

def ensure_admin():
    admin = session.query(Teacher).filter(Teacher.email == ADMIN_EMAIL).first()
    if not admin:
        admin = Teacher(name="Administrador", email=ADMIN_EMAIL, password=ADMIN_PASSWORD)
        session.add(admin)
        session.commit()

ensure_admin()

# ---------------------------------
# UI: sessão de autenticação básica
# ---------------------------------
st.set_page_config(page_title="Submissões – Industrial & EBC II (2025/2)", layout="wide")

if 'user_id' not in st.session_state:
    st.session_state.user_id = None
    st.session_state.user_role = None   # 'student' ou 'teacher'
    st.session_state.user_name = None

# Quick login (para testes)
if st.session_state.user_id is None and DEV_QUICK_LOGIN:
    admin_user = session.query(Teacher).filter(Teacher.email == ADMIN_EMAIL).first()
    if admin_user:
        st.session_state.user_id = admin_user.id
        st.session_state.user_role = 'teacher'
        st.session_state.user_name = admin_user.name
        st.info(f"Login automático como admin ({ADMIN_EMAIL}) ativado (DEV_QUICK_LOGIN).")
        st.experimental_rerun()

# Login
if st.session_state.user_id is None:
    st.title("Login – Submissões (Industrial & EBC II)")
    login_email = st.text_input("E-mail institucional").strip().lower()
    login_pass  = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        user = session.query(Teacher).filter_by(email=login_email, password=login_pass).first()
        role = 'teacher'
        if user is None:
            user = session.query(Student).filter_by(email=login_email, password=login_pass).first()
            role = 'student'
        if user:
            st.session_state.user_id = user.id
            st.session_state.user_role = role
            st.session_state.user_name = user.name
            st.experimental_rerun()
        else:
            st.error("Credenciais inválidas.")
    st.stop()

# Usuário corrente
if st.session_state.user_role == 'teacher':
    current_user = session.query(Teacher).get(st.session_state.user_id)
else:
    current_user = session.query(Student).get(st.session_state.user_id)

# Sidebar
st.sidebar.write(f"**Usuário:** {st.session_state.user_name} ({'Docente/Admin' if st.session_state.user_role=='teacher' else 'Aluno'})")
if st.sidebar.button("Sair"):
    for k in list(st.session_state.keys()):
        st.session_state.pop(k)
    st.experimental_rerun()

# ---------------------------
# Abas por papel do usuário
# ---------------------------
if st.session_state.user_role == 'student':
    tabs = st.tabs(["Grupos & Temas", "Upload"])
else:
    tabs = st.tabs(["Grupos & Temas", "Upload", "Avaliação", "Relatórios", "Admin (Students)"])

# ---------------------------
# Tab 1 – Grupos & Temas
# ---------------------------
with tabs[0]:
    st.header("Grupos & Temas (sem duplicidade de tema)")
    if st.session_state.user_role == 'student':
        # Já está em grupo?
        membership = session.query(GroupMember).filter_by(student_id=current_user.id).first()
        if membership:
            grp = session.query(Group).get(membership.group_id)
            st.subheader(f"Seu Grupo: {grp.name}")
            st.write(f"**Tema:** {grp.theme}")
            # membros
            st.markdown("**Integrantes:**")
            lines = []
            for gm in grp.members_assoc:
                stud = gm.student
                tags = []
                if stud.industrial_class_id: tags.append("IND")
                if stud.ebc_class_id: tags.append("EBC")
                t = "/".join(tags) if tags else "–"
                lines.append(f"- {stud.name} ({t})")
            st.markdown("\n".join(lines))
            st.info("Para enviar arquivos, acesse a aba **Upload**.")
        else:
            st.subheader("Entrar em um grupo existente")
            groups = session.query(Group).all()
            joinables = []
            for g in groups:
                n = len(g.members_assoc)
                if n < 5:  # até 5 alunos
                    joinables.append(f"{g.name} | {g.theme} ({n}/5)")
            if joinables:
                choice = st.selectbox("Escolha um grupo para entrar:", [""] + joinables, index=0)
                if choice:
                    gname = choice.split(" | ")[0]
                    grp = session.query(Group).filter_by(name=gname).first()
                    if grp:
                        if len(grp.members_assoc) >= 5:
                            st.error("Grupo já atingiu o limite de 5 integrantes.")
                        else:
                            session.add(GroupMember(group_id=grp.id, student_id=current_user.id))
                            session.commit()
                            st.success(f"Você entrou no {grp.name}.")
                            st.experimental_rerun()
            else:
                st.write("_Não há grupos com vagas._")

            st.markdown("---")
            st.subheader("Criar novo grupo")
            new_theme = st.text_input("Tema (sem duplicidade; será bloqueado ao primeiro grupo que escolher):")
            if st.button("Criar Grupo"):
                if not new_theme.strip():
                    st.error("Informe um tema.")
                else:
                    # Sem duplicidade (mesmo título)
                    same = session.query(Group).filter(Group.theme.ilike(new_theme.strip())).first()
                    if same:
                        st.error("Tema já escolhido por outro grupo.")
                    else:
                        # turma primária = oferta do criador (prioriza Industrial, senão EBC)
                        primary_off = current_user.industrial_class or current_user.ebc_class
                        base = primary_off.name if primary_off else "GRP"
                        # Próximo número
                        existing = [g for g in session.query(Group).all() if g.name.startswith(f"Grupo {base}-")]
                        next_num = len(existing) + 1
                        gname = f"Grupo {base}-{next_num}"
                        g = Group(name=gname, theme=new_theme.strip(),
                                  primary_offering_id=primary_off.id if primary_off else None)
                        session.add(g); session.commit()
                        session.add(GroupMember(group_id=g.id, student_id=current_user.id)); session.commit()
                        st.success(f"Grupo **{gname}** criado.")
                        st.experimental_rerun()
    else:
        st.write("**Todos os grupos:**")
        groups = session.query(Group).all()
        if not groups:
            st.write("_Nenhum grupo ainda._")
        for g in groups:
            names = ", ".join([gm.student.name for gm in g.members_assoc]) or "–"
            st.write(f"- **{g.name}** – Tema: *{g.theme}* – Integrantes: {names}")

# ---------------------------
# Tab 2 – Upload (Aluno)
# ---------------------------
with tabs[1]:
    st.header("Upload de Entregáveis")
    if st.session_state.user_role != 'student':
        st.info("Apenas alunos enviam arquivos.")
    else:
        membership = session.query(GroupMember).filter_by(student_id=current_user.id).first()
        if not membership:
            st.warning("Entre em um grupo primeiro.")
        else:
            grp = session.query(Group).get(membership.group_id)
            st.subheader(f"{grp.name} — {grp.theme}")

            # Descobrir turmas do aluno
            ind_off = current_user.industrial_class
            ebc_off = current_user.ebc_class

            # Disciplina da entrega
            disc_choices = []
            if ind_off: disc_choices.append(("Economia Industrial", "IND", ind_off))
            if ebc_off: disc_choices.append(("Economia Brasileira Contemporânea II", "EBCII", ebc_off))
            disc_label_default = disc_choices[0][0] if disc_choices else "Economia Industrial"
            disc_label = st.selectbox(
                "Disciplina da entrega",
                [x[0] for x in disc_choices] if disc_choices else [disc_label_default]
            )
            disc_code, disc_off = None, None
            for lbl, code, off in disc_choices:
                if lbl == disc_label:
                    disc_code, disc_off = code, off
                    break

            # Turma da entrega (prefill)
            turma_default = disc_off.name if disc_off else (ind_off.name if ind_off else (ebc_off.name if ebc_off else ""))
            turma_entrega = st.text_input("Turma da entrega (ex.: MA6, MB6, NA6, NB6)", value=turma_default)

            terms  = st.file_uploader("Termo de Cessão (docx/pdf) **obrigatório**", type=["docx", "pdf"])
            report = st.file_uploader("Relatório (pdf/docx) **obrigatório**", type=["pdf", "docx"])
            slides = st.file_uploader("Slides (pptx/pdf) **obrigatório**", type=["pptx", "pdf"])
            video  = st.file_uploader("Vídeo (mp4/mov/mkv/avi/mpeg) **obrigatório**", type=["mp4","mov","mkv","avi","mpeg"])

            if st.button("Enviar"):
                if not all([terms, report, slides, video]):
                    st.error("Envie todos os arquivos obrigatórios.")
                elif not turma_entrega.strip():
                    st.error("Informe a turma da entrega.")
                else:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    base_dir = os.path.join("uploads", f"group_{grp.id}", timestamp)
                    os.makedirs(base_dir, exist_ok=True)

                    files = [("termo", terms), ("relatorio", report), ("slides", slides), ("video", video)]
                    saved = {}
                    # salva local
                    for label, up in files:
                        path = os.path.join(base_dir, up.name)
                        with open(path, "wb") as f:
                            f.write(up.getbuffer())
                        saved[label] = up.name

                    # metadados úteis da entrega
                    saved["_meta"] = {
                        "disciplina": disc_label,
                        "disciplina_code": disc_code,
                        "turma": turma_entrega.strip()
                    }

                    # SharePoint (opcional)
                    sp_client_id     = st.secrets.get("sp_client_id")
                    sp_client_secret = st.secrets.get("sp_client_secret")
                    sp_tenant_id     = st.secrets.get("sp_tenant_id")
                    sp_drive_id      = st.secrets.get("sp_drive_id")  # ID do drive (biblioteca)
                    sp_folder_path   = st.secrets.get("sp_folder_path", "/Shared Documents/Submissoes_2025_2")
                    token = None
                    if sp_client_id and sp_client_secret and sp_tenant_id:
                        try:
                            tok_url = f"https://login.microsoftonline.com/{sp_tenant_id}/oauth2/v2.0/token"
                            data = {
                                "client_id": sp_client_id,
                                "client_secret": sp_client_secret,
                                "grant_type": "client_credentials",
                                "scope": "https://graph.microsoft.com/.default",
                            }
                            r = requests.post(tok_url, data=data, timeout=30)
                            r.raise_for_status()
                            token = r.json().get("access_token")
                        except Exception as e:
                            st.error(f"Auth SharePoint falhou: {e}")

                    if token and sp_drive_id:
                        for label, up in files:
                            local_path = os.path.join(base_dir, up.name)
                            try:
                                enc = urllib.parse.quote(up.name)
                                url = f"https://graph.microsoft.com/v1.0/drives/{sp_drive_id}/root:{sp_folder_path}/{enc}:/createUploadSession"
                                h = {"Authorization": f"Bearer {token}"}
                                s = requests.post(url, headers=h, timeout=30)
                                s.raise_for_status()
                                upload_url = s.json().get("uploadUrl")
                                size = os.path.getsize(local_path)
                                with open(local_path, "rb") as fh:
                                    chunk = 5 * 1024 * 1024
                                    sent = 0
                                    while True:
                                        data = fh.read(chunk)
                                        if not data: break
                                        rng = f"bytes {sent}-{sent+len(data)-1}/{size}"
                                        hh = {"Authorization": f"Bearer {token}",
                                              "Content-Length": str(len(data)),
                                              "Content-Range": rng}
                                        put = requests.put(upload_url, headers=hh, data=data, timeout=120)
                                        if put.status_code not in (200,201,202):
                                            raise Exception(f"Upload falhou: {put.status_code} {put.text}")
                                        sent += len(data)
                            except Exception as e:
                                st.error(f"SharePoint falhou para {up.name}: {e}")

                    # Grava submissão
                    sub = Submission(group_id=grp.id, timestamp=timestamp, files=json.dumps(saved, ensure_ascii=False))
                    session.add(sub); session.commit()
                    st.success("Submissão registrada. Arquivos salvos localmente e (se configurado) no SharePoint.")

# ---------------------------
# Tab 3 – Avaliação (Docente)
# ---------------------------
if st.session_state.user_role == 'teacher':
    with tabs[2]:
        st.header("Avaliação de Grupos")
        teacher_offs = current_user.offerings  # turmas do docente
        all_groups = session.query(Group).all()
        is_admin = (getattr(current_user, "email", "").lower() == ADMIN_EMAIL.lower())

        # Filtros
        class_opts = ["Todas"] + ([o.name for o in teacher_offs] if not is_admin else sorted({o.name for o in session.query(Offering).all()}))
        sel_class = st.selectbox("Turma", class_opts)

        if is_admin:
            disc_opts = ["Todas", "Economia Industrial", "Economia Brasileira Contemporânea II"]
            sel_disc = st.selectbox("Disciplina", disc_opts)
        else:
            discs = list({o.discipline.name for o in teacher_offs})
            sel_disc = st.selectbox("Disciplina", ["Todas"] + discs)

        # docente vê tudo; admin também. Filtros só para navegação.
        filtered = []
        for g in all_groups:
            ok = True
            if sel_class != "Todas":
                # grupo deve ter pelo menos 1 aluno dessa turma
                has = False
                for gm in g.members_assoc:
                    stud = gm.student
                    if (stud.industrial_class and stud.industrial_class.name == sel_class) or \
                       (stud.ebc_class and stud.ebc_class.name == sel_class):
                        has = True; break
                ok = has
            if ok and sel_disc != "Todas":
                # grupo deve ter aluno naquela disciplina (IND/EBC)
                hasd = False
                for gm in g.members_assoc:
                    stud = gm.student
                    if sel_disc.startswith("Economia Industrial") and stud.industrial_class_id:
                        hasd = True; break
                    if sel_disc.startswith("Economia Brasileira") and stud.ebc_class_id:
                        hasd = True; break
                ok = hasd
            if ok: filtered.append(g)

        filtered = sorted({g for g in filtered}, key=lambda x: x.name)

        if not filtered:
            st.write("_Nenhum grupo para os filtros._")
        else:
            gnames = [""] + [g.name for g in filtered]
            gsel = st.selectbox("Grupo", gnames)
            if gsel:
                grp = session.query(Group).filter_by(name=gsel).first()
                st.subheader(f"{grp.name} — {grp.theme}")

                st.markdown("**Integrantes:**")
                for gm in grp.members_assoc:
                    stud = gm.student
                    tags = []
                    if stud.industrial_class_id: tags.append("IND")
                    if stud.ebc_class_id: tags.append("EBC")
                    t = "/".join(tags) if tags else "–"
                    st.write(f"- {stud.name} ({t})")

                # histórico
                subs = session.query(Submission).filter_by(group_id=grp.id).order_by(Submission.id.desc()).all()
                if subs:
                    st.markdown("**Histórico de envios:**")
                    for s in subs:
                        when = s.timestamp
                        files = json.loads(s.files)
                        st.write(f"📄 {when} — {files.get('_meta', {})}")
                        for lbl, fn in files.items():
                            if lbl == "_meta": continue
                            path = os.path.join("uploads", f"group_{grp.id}", when, fn)
                            if os.path.exists(path):
                                with open(path, "rb") as f:
                                    data = f.read()
                                nice = {"termo":"Termo", "relatorio":"Relatório", "slides":"Slides", "video":"Vídeo"}.get(lbl,lbl)
                                st.download_button(f"Baixar {nice} ({fn})", data=data, file_name=fn, key=f"d_{s.id}_{lbl}")
                            else:
                                st.write(f"- {fn} (não encontrado localmente)")
                else:
                    st.write("_Sem envios._")

                st.markdown("---")
                st.markdown("**Avaliação**")
                # O que este docente pode avaliar? (se admin: tudo)
                teaches_ind = is_admin or any(o.discipline_id == disc_ind.id for o in teacher_offs)
                teaches_ebc = is_admin or any(o.discipline_id == disc_ebc.id for o in teacher_offs)

                ind_grade = ebc_grade = ind_comment = ebc_comment = None
                ind_ok = ebc_ok = None
                if teaches_ind:
                    ind_grade   = st.text_input("Nota – Economia Industrial", value=grp.industrial_grade or "")
                    ind_comment = st.text_area("Comentários – Economia Industrial", value=grp.industrial_comment or "")
                    ind_ok      = st.checkbox("Aprovar (Industrial)", value=bool(grp.industrial_approved))
                if teaches_ebc:
                    ebc_grade   = st.text_input("Nota – EBC II", value=grp.ebc_grade or "")
                    ebc_comment = st.text_area("Comentários – EBC II", value=grp.ebc_comment or "")
                    ebc_ok      = st.checkbox("Aprovar (EBC II)", value=bool(grp.ebc_approved))

                # participação por aluno
                st.markdown("**Participação (opcional, por integrante):**")
                part_inputs = {}
                for gm in grp.members_assoc:
                    lab = f"{gm.student.name}"
                    part_inputs[gm.id] = st.text_input(lab, value=gm.participation or "", key=f"p_{gm.id}")

                if st.button("Salvar avaliação"):
                    if teaches_ind:
                        grp.industrial_grade = ind_grade.strip() if ind_grade else None
                        grp.industrial_comment = ind_comment.strip() if ind_comment else None
                        grp.industrial_approved = bool(ind_ok)
                    if teaches_ebc:
                        grp.ebc_grade = ebc_grade.strip() if ebc_grade else None
                        grp.ebc_comment = ebc_comment.strip() if ebc_comment else None
                        grp.ebc_approved = bool(ebc_ok)
                    for gm in grp.members_assoc:
                        gm.participation = part_inputs.get(gm.id, "").strip() or None
                    session.commit()
                    st.success("Avaliação salva.")

# ---------------------------
# Tab 4 – Relatórios (Doc.)
# ---------------------------
if st.session_state.user_role == 'teacher':
    with tabs[3]:
        st.header("Relatórios (PDF)")
        if FPDF is None:
            st.error("A biblioteca fpdf não está instalada. Adicione 'fpdf' ao requirements.txt para habilitar PDFs.")
        else:
            from io import BytesIO
            import zipfile

            col1, col2, col3 = st.columns(3)
            b1 = col1.button("PDF por Grupo (zip)")
            b2 = col2.button("PDF por Aluno (zip)")
            b3 = col3.button("PDF Resumo do Docente")

            is_admin = (getattr(current_user, "email", "").lower() == ADMIN_EMAIL.lower())

            def pdf_bytes_for_group(grp: Group):
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Arial", 'B', 14)
                pdf.cell(0, 10, f"Grupo {grp.name}", ln=True, align="C")
                pdf.set_font("Arial", '', 12)
                pdf.cell(0, 8, f"Tema: {grp.theme}", ln=True)
                pdf.ln(2)
                pdf.cell(0, 8, "Integrantes:", ln=True)
                for gm in grp.members_assoc:
                    stud = gm.student
                    tags = []
                    if stud.industrial_class_id: tags.append("IND")
                    if stud.ebc_class_id: tags.append("EBC")
                    t = "/".join(tags) if tags else "–"
                    line = f" - {stud.name} ({t})"
                    if gm.participation:
                        line += f" | Participação: {gm.participation}"
                    pdf.cell(0, 8, line, ln=True)
                pdf.ln(3)
                pdf.cell(0, 8, "Avaliações:", ln=True)
                if grp.industrial_grade:
                    pdf.cell(0, 8, f" - IND: {grp.industrial_grade}", ln=True)
                if grp.industrial_comment:
                    pdf.multi_cell(0, 8, f"   Comentário IND: {grp.industrial_comment}")
                if grp.ebc_grade:
                    pdf.cell(0, 8, f" - EBC II: {grp.ebc_grade}", ln=True)
                if grp.ebc_comment:
                    pdf.multi_cell(0, 8, f"   Comentário EBC II: {grp.ebc_comment}")
                return pdf.output(dest="S").encode("latin-1")

            if b1:
                groups = session.query(Group).all()
                if not is_admin:
                    t_off_ids = {o.id for o in current_user.offerings}
                    keep = []
                    for g in groups:
                        has = False
                        for gm in g.members_assoc:
                            s = gm.student
                            if (s.industrial_class_id in t_off_ids) or (s.ebc_class_id in t_off_ids):
                                has = True; break
                        if has: keep.append(g)
                    groups = keep
                buf = BytesIO()
                with zipfile.ZipFile(buf, "w") as z:
                    for g in groups:
                        z.writestr(f"{g.name.replace(' ','_')}.pdf", pdf_bytes_for_group(g))
                st.download_button("Baixar ZIP (Grupos)", data=buf.getvalue(), file_name="Relatorios_Grupos.zip")

            if b2:
                students = session.query(Student).all()
                if not is_admin:
                    t_off_ids = {o.id for o in current_user.offerings}
                    students = [s for s in students if (s.industrial_class_id in t_off_ids) or (s.ebc_class_id in t_off_ids)]
                buf = BytesIO()
                with zipfile.ZipFile(buf, "w") as z:
                    for s in students:
                        pdf = FPDF(); pdf.add_page()
                        pdf.set_font("Arial", 'B', 14)
                        pdf.cell(0, 10, f"Relatório Individual – {s.name}", ln=True, align="C")
                        pdf.set_font("Arial", '', 12)
                        pdf.cell(0, 8, f"E-mail: {s.email}  RA: {s.ra or '-'}", ln=True)
                        if s.industrial_class: pdf.cell(0, 8, f" - IND: {s.industrial_class.name}", ln=True)
                        if s.ebc_class: pdf.cell(0, 8, f" - EBC II: {s.ebc_class.name}", ln=True)
                        mems = session.query(GroupMember).filter_by(student_id=s.id).all()
                        for gm in mems:
                            g = gm.group
                            pdf.ln(2)
                            pdf.cell(0, 8, f"Grupo {g.name} — Tema: {g.theme}", ln=True)
                            if s.industrial_class_id and g.industrial_grade:
                                pdf.cell(0, 8, f"   Nota IND: {g.industrial_grade}", ln=True)
                            if s.ebc_class_id and g.ebc_grade:
                                pdf.cell(0, 8, f"   Nota EBC II: {g.ebc_grade}", ln=True)
                            if gm.participation:
                                pdf.multi_cell(0, 8, f"   Participação: {gm.participation}")
                        z.writestr(f"{s.name.replace(' ','_')}.pdf", pdf.output(dest="S").encode("latin-1"))
                st.download_button("Baixar ZIP (Individuais)", data=buf.getvalue(), file_name="Relatorios_Individuais.zip")

            if b3:
                pdf = FPDF(); pdf.add_page()
                pdf.set_font("Arial", 'B', 16)
                pdf.cell(0, 10, f"Resumo – {current_user.name}", ln=True, align="C")
                pdf.set_font("Arial", '', 12)
                offs = current_user.offerings if not is_admin else session.query(Offering).all()
                for o in offs:
                    pdf.ln(4)
                    pdf.cell(0, 8, f"Turma {o.name} – {o.discipline.name}", ln=True)
                    s_ids = [s.id for s in session.query(Student).filter(
                        (Student.industrial_class_id == o.id) | (Student.ebc_class_id == o.id)
                    ).all()]
                    g_ids = {gm.group_id for gm in session.query(GroupMember).filter(GroupMember.student_id.in_(s_ids)).all()}
                    if not g_ids:
                        pdf.cell(0, 8, "  (Sem grupos)", ln=True)
                    else:
                        for gid in sorted(g_ids):
                            g = session.query(Group).get(gid)
                            pdf.cell(0, 8, f"  - {g.name}: ", ln=True)
                            if o.discipline_id == disc_ind.id and g.industrial_grade:
                                pdf.cell(0, 8, f"     Nota IND: {g.industrial_grade}", ln=True)
                            if o.discipline_id == disc_ebc.id and g.ebc_grade:
                                pdf.cell(0, 8, f"     Nota EBC II: {g.ebc_grade}", ln=True)
                st.download_button("Baixar PDF (Resumo)", data=pdf.output(dest="S").encode("latin-1"),
                                   file_name=f"Resumo_{current_user.name.replace(' ','_')}.pdf")

# ---------------------------
# Tab 5 – Admin (Students)
# ---------------------------
if st.session_state.user_role == 'teacher':
    with tabs[4]:
        st.header("Admin (Students)")
        st.info(
            "Faça upload de um CSV por turma. Formato mínimo: "
            "**name,ra[,email]**. Se o email vier vazio, uso **RA@pucsp.edu.br**.\n\n"
            "Selecione abaixo a **Disciplina** e a **Turma** que este arquivo representa."
        )

        # Seleção da disciplina e turma
        disc_label_to_obj = {
            "Economia Industrial": disc_ind,
            "Economia Brasileira Contemporânea II": disc_ebc,
        }
        sel_disc_label = st.selectbox("Disciplina deste CSV", list(disc_label_to_obj.keys()))
        sel_disc = disc_label_to_obj[sel_disc_label]
        sel_turma = st.text_input("Turma (ex.: MA6, MB6, NA6, NB6)")

        up = st.file_uploader("CSV (colunas: name,ra[,email])", type=["csv"])

        if up and st.button("Processar CSV desta turma"):
            try:
                import pandas as pd
                if not sel_turma.strip():
                    st.error("Informe a turma.")
                    st.stop()

                # garante oferta (Offering) da disciplina/turma
                off = session.query(Offering).filter_by(name=sel_turma.strip(), discipline_id=sel_disc.id).first()
                if not off:
                    off = Offering(name=sel_turma.strip(), discipline_id=sel_disc.id, teacher_id=None)
                    session.add(off); session.commit()

                df = pd.read_csv(up).fillna("")
                created = updated = 0
                for _, row in df.iterrows():
                    name = str(row.get("name","")).strip()
                    ra   = str(row.get("ra","")).strip()
                    email_raw = str(row.get("email","")).strip().lower()
                    if not name or not ra:
                        continue
                    email = email_raw if email_raw else f"{ra}@pucsp.edu.br"

                    # normaliza para minúsculas
                    email = email.lower()

                    stu = session.query(Student).filter_by(email=email).first()
                    if not stu:
                        # senha inicial = RA (ou '1234' se vazia)
                        password = ra if ra else "1234"
                        stu = Student(
                            name=name, email=email, ra=ra or None, password=password,
                            industrial_class_id=off.id if sel_disc.id == disc_ind.id else None,
                            ebc_class_id=off.id if sel_disc.id == disc_ebc.id else None
                        )
                        session.add(stu); session.commit()
                        created += 1
                    else:
                        # atualiza nome/RA e matricula na oferta correspondente
                        stu.name = name
                        if ra: stu.ra = ra
                        if sel_disc.id == disc_ind.id:
                            stu.industrial_class_id = off.id
                        else:
                            stu.ebc_class_id = off.id
                        session.commit()
                        updated += 1

                st.success(f"Processado: {created} criados, {updated} atualizados para {sel_disc_label} – {sel_turma}.")
            except Exception as e:
                st.error(f"Falha ao importar CSV: {e}")

        st.markdown("---")
        st.subheader("Vincular docentes às turmas (ofertas)")
        offs = session.query(Offering).all()
        if offs:
            # lista simples para escolher oferta e docente
            off_names = [f"{o.name} – {o.discipline.name}" for o in offs]
            sel = st.selectbox("Oferta", [""] + off_names)
            teachers = session.query(Teacher).all()
            t_names = [f"{t.name} <{t.email}>" for t in teachers]
            sel_t = st.selectbox("Docente", [""] + t_names)
            if st.button("Salvar vínculo"):
                try:
                    if sel and sel_t:
                        o_name = sel.split(" – ")[0]
                        o = session.query(Offering).filter_by(name=o_name).first()
                        t_email = sel_t.split("<")[-1].rstrip(">").lower()
                        t = session.query(Teacher).filter_by(email=t_email).first()
                        o.teacher_id = t.id
                        session.commit()
                        st.success("Vínculo atualizado.")
                    else:
                        st.warning("Selecione oferta e docente.")
                except Exception as e:
                    st.error(f"Erro: {e}")
        else:
            st.info("Sem ofertas cadastradas ainda. Importe alunos primeiro para criar ofertas automaticamente.")

st.caption("MVP – Submissões Industrial & EBC II (2025/2)")
