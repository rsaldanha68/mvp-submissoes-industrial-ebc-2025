# app.py – MVP Submissões Industrial & EBC II (2025/2) – consolidado
import os, io, re, json, urllib
from datetime import datetime
from typing import Optional

import streamlit as st
import pandas as pd
import requests

# PDFs (opcional)
try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

# ----------------------------
# Banco via SQLAlchemy (SQLite)
# ----------------------------
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, ForeignKey, Text, UniqueConstraint, inspect
)
from sqlalchemy.orm import sessionmaker, relationship, declarative_base

DB_PATH = os.path.join(os.getcwd(), "submissions_app.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
Base = declarative_base()
Session = sessionmaker(bind=engine)
session = Session()

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
    email = Column(String, unique=True, nullable=False)     # salvo em minúsculas
    password = Column(String, nullable=False)               # (produção: usar hash!)
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
    email = Column(String, unique=True, nullable=False)     # salvo em minúsculas
    password = Column(String, nullable=False)               # (produção: hash)
    industrial_class_id = Column(Integer, ForeignKey('offerings.id'))
    ebc_class_id = Column(Integer, ForeignKey('offerings.id'))
    industrial_class = relationship("Offering", foreign_keys=[industrial_class_id])
    ebc_class = relationship("Offering", foreign_keys=[ebc_class_id])
    groups_assoc = relationship("GroupMember", back_populates="student")
    groups = relationship(
        "Group",
        secondary="group_members",
        back_populates="members",
        overlaps="groups_assoc"
    )

class Group(Base):
    __tablename__ = 'groups'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)      # "Grupo MA6-1", etc.
    theme = Column(String, nullable=False)     # sem duplicidade (app garante)
    primary_offering_id = Column(Integer, ForeignKey('offerings.id'))
    primary_offering = relationship("Offering")

    # NOVOS CAMPOS
    allow_six = Column(Boolean, default=False)        # permite 6º aluno, se docente autorizar
    publish_public = Column(Boolean, default=False)   # publicar na galeria pública

    # Avaliação (por UC)
    industrial_grade = Column(String)
    ebc_grade = Column(String)
    industrial_comment = Column(Text)
    ebc_comment = Column(Text)
    industrial_approved = Column(Boolean, default=False)
    ebc_approved = Column(Boolean, default=False)
    members_assoc = relationship(
        "GroupMember",
        back_populates="group",
        overlaps="members"
    )
    members = relationship(
        "Student",
        secondary="group_members",
        back_populates="groups",
        overlaps="members_assoc,groups_assoc"
    )

class GroupMember(Base):
    __tablename__ = 'group_members'
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('groups.id'), nullable=False)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False)
    participation = Column(Text)
    group = relationship(
        "Group",
        back_populates="members_assoc",
        overlaps="members,groups"
    )
    student = relationship(
        "Student",
        back_populates="groups_assoc",
        overlaps="groups,members"
    )

class Submission(Base):
    __tablename__ = 'submissions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('groups.id'), nullable=False)
    timestamp = Column(String, nullable=False)  # "20250817_103501"
    files = Column(Text, nullable=False)        # JSON: {"termo":0/1, "relatorio":"...", "slides":"...", "video_file":"...", "_meta":{...}, "_by":"email"}
    group = relationship("Group")

class Theme(Base):
    __tablename__ = 'themes'
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)        # título único
    category = Column(String, nullable=True)      # opcional (Privatização, Concessão, PPP, etc.)
    active = Column(Boolean, default=True)
    __table_args__ = (UniqueConstraint('title', name='_uq_theme_title'),)

Base.metadata.create_all(engine)

# --- MIGRAÇÃO SIMPLES: novas colunas allow_six, publish_public (se não existirem) ---
with engine.connect() as conn:
    insp = inspect(conn)
    cols = [c['name'] for c in insp.get_columns('groups')]
    if 'allow_six' not in cols:
        try:
            conn.exec_driver_sql("ALTER TABLE groups ADD COLUMN allow_six INTEGER DEFAULT 0")
        except Exception:
            pass
    if 'publish_public' not in cols:
        try:
            conn.exec_driver_sql("ALTER TABLE groups ADD COLUMN publish_public INTEGER DEFAULT 0")
        except Exception:
            pass

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
ADMIN_EMAIL = (st.secrets.get("ADMIN_EMAIL", "rsaldanha@pucsp.edu.br") or "").lower()
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "8722")
DEV_QUICK_LOGIN = bool(st.secrets.get("DEV_QUICK_LOGIN", False))

def ensure_admin():
    admin = session.query(Teacher).filter(Teacher.email == ADMIN_EMAIL).first()
    if not admin:
        admin = Teacher(name="Administrador", email=ADMIN_EMAIL.lower(), password=ADMIN_PASSWORD)
        session.add(admin)
        session.commit()
ensure_admin()

# ---------------------------------
# UI / GALERIA PÚBLICA
# ---------------------------------
st.set_page_config(page_title="Submissões – Industrial & EBC II (2025/2)", layout="wide")

# --- Galeria pública (sem login) ---
if st.query_params.get("public", ["0"])[0] == "1":
    st.title("Galeria Pública – Trabalhos (IND + EBC II)")
    search = st.text_input("Buscar por tema ou integrante")
    pubs = session.query(Group).filter(Group.publish_public == True).order_by(Group.name.asc()).all()
    if search:
        s = search.strip().lower()
        def match(g):
            if s in (g.theme or "").lower(): return True
            for gm in g.members_assoc:
                if s in (gm.student.name or "").lower(): return True
            return False
        pubs = [g for g in pubs if match(g)]
    if not pubs:
        st.info("Ainda não há trabalhos publicados.")
        st.stop()
    for g in pubs:
        st.markdown(f"### {g.name} — *{g.theme}*")
        names = ", ".join(sorted([gm.student.name for gm in g.members_assoc]))
        st.caption(f"Integrantes: {names if names else '—'}")
        sub = session.query(Submission).filter_by(group_id=g.id).order_by(Submission.id.desc()).first()
        if sub:
            files = json.loads(sub.files)
            meta = files.get("_meta", {})
            video_url = meta.get("video_url", "")
            if video_url:
                st.write(f"🎬 Vídeo: {video_url}")
                if "youtube.com" in video_url or "youtu.be" in video_url:
                    st.video(video_url)
            # botões de download dos anexos
            for lbl, fn in files.items():
                if lbl in ("_meta","_by","termo","video_file"): 
                    continue
                p = os.path.join("uploads", f"group_{g.id}", sub.timestamp, fn)
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        st.download_button(f"Baixar {lbl.capitalize()} ({fn})", data=f.read(), file_name=fn, key=f"pub_{g.id}_{lbl}")
        st.markdown("---")
    st.stop()

# ---------------------------------
# Sessão (login)
# ---------------------------------
if 'user_id' not in st.session_state:
    st.session_state.user_id = None
    st.session_state.user_role = None
    st.session_state.user_name = None

# Quick login (para testes)
if st.session_state.user_id is None and DEV_QUICK_LOGIN:
    admin_user = session.query(Teacher).filter(Teacher.email == ADMIN_EMAIL).first()
    if admin_user:
        st.session_state.user_id = admin_user.id
        st.session_state.user_role = 'teacher'
        st.session_state.user_name = admin_user.name
        st.info(f"Login automático como admin ({ADMIN_EMAIL}) ativado (DEV_QUICK_LOGIN).")
        st.rerun()

# Login
if st.session_state.user_id is None:
    st.title("Login – Submissões (Industrial & EBC II)")
    login_email = st.text_input("E-mail institucional (ex.: ra123456@pucsp.edu.br)").strip().lower()
    login_pass  = st.text_input("Senha", type="password")
    st.caption("Alunos: use seu e-mail institucional; senha inicial = **RA** (se o docente não alterar). Docentes: senha definida no cadastro.")
    if st.button("Entrar"):
        user = session.query(Teacher).filter(
            Teacher.email == login_email,
            Teacher.password == login_pass
        ).first()
        role = 'teacher'
        if user is None:
            user = session.query(Student).filter(
                Student.email == login_email,
                Student.password == login_pass
            ).first()
            role = 'student'
        if user:
            st.session_state.user_id = user.id
            st.session_state.user_role = role
            st.session_state.user_name = user.name
            st.rerun()
        else:
            st.error("Credenciais inválidas.")
    st.stop()

# Usuário corrente
if st.session_state.user_role == 'teacher':
    current_user = session.query(Teacher).get(st.session_state.user_id)
else:
    current_user = session.query(Student).get(st.session_state.user_id)

st.sidebar.write(f"**Usuário:** {st.session_state.user_name} ({'Docente/Admin' if st.session_state.user_role=='teacher' else 'Aluno'})")
if st.sidebar.button("Sair"):
    for k in list(st.session_state.keys()):
        st.session_state.pop(k)
    st.rerun()

# ---------------------------
# Helpers utilitários
# ---------------------------
DISC_MAP = {
    "IND": "Economia Industrial",
    "EBC II": "Economia Brasileira Contemporânea II",
    "EBC": "Economia Brasileira Contemporânea II",
}

def parse_filemeta(fname: str):
    base = os.path.splitext(os.path.basename(fname))[0]
    parts = base.split()
    turma = None
    for p in parts:
        if re.fullmatch(r"[A-Z]{2}6", p.upper()):
            turma = p.upper(); break
    joined = " ".join(parts).upper()
    disc_token = None
    if "EBC II" in joined: disc_token = "EBC II"
    elif re.search(r"\bIND\b", joined): disc_token = "IND"
    elif re.search(r"\bEBC\b", joined): disc_token = "EBC"
    docente = None
    if disc_token:
        pos = joined.find(disc_token)
        docente_raw = base[pos + len(disc_token):].strip()
        docente_raw = re.sub(r"\s+", " ", docente_raw).strip()
        docente = docente_raw if docente_raw else None
    disc_label = DISC_MAP.get(disc_token or "", None)
    return turma, disc_label, docente

def get_or_create_teacher(session, nome: str, email_guess: str = "") -> Teacher:
    email_norm = (email_guess or "").strip().lower()
    if not email_norm and nome:
        slug = re.sub(r"[^a-z0-9]+", ".", (nome or "").lower()).strip(".")
        email_norm = f"{slug}@pucsp.edu.br"
    if not email_norm: email_norm = "docente@pucsp.edu.br"
    t = session.query(Teacher).filter(Teacher.email == email_norm).first()
    if not t:
        t = Teacher(name=(nome or email_norm.split("@")[0].title()).strip(),
                    email=email_norm, password="1234")
        session.add(t); session.commit()
    return t

def get_or_create_offering(session, disc_label: str, turma: str, teacher: Optional[Teacher]) -> Offering:
    disc = session.query(Discipline).filter(Discipline.name == disc_label).first()
    if not disc:
        disc = Discipline(name=disc_label); session.add(disc); session.commit()
    off = session.query(Offering).filter_by(name=turma, discipline_id=disc.id).first()
    if not off:
        off = Offering(name=turma, discipline_id=disc.id, teacher_id=teacher.id if teacher else None)
        session.add(off); session.commit()
    else:
        if teacher and off.teacher_id != teacher.id:
            off.teacher_id = teacher.id
            session.commit()
    return off

def import_students_df(session, df: pd.DataFrame, offering: Offering, is_industrial: bool):
    cols = {c.lower().strip(): c for c in df.columns}
    def pick(*opts):
        for o in opts:
            if o in cols: return cols[o]
        return None
    c_name = pick("name","nome","aluno")
    c_ra   = pick("ra","registro","matricula","mtr","rm")
    c_email= pick("email","e-mail")
    if not c_name or not c_ra:
        raise ValueError("A planilha precisa conter ao menos as colunas 'name'/'nome' e 'ra'.")
    created = updated = 0
    for _, row in df.iterrows():
        name = str(row[c_name]).strip()
        ra   = str(row[c_ra]).strip()
        email = str(row[c_email]).strip().lower() if c_email else ""
        if not name or not ra: continue
        if not email: email = f"{ra}@pucsp.edu.br"
        email = email.lower()
        stu = session.query(Student).filter_by(email=email).first()
        if not stu:
            stu = Student(
                name=name, email=email, ra=ra or None, password=(ra or "1234"),
                industrial_class_id=offering.id if is_industrial else None,
                ebc_class_id=offering.id if not is_industrial else None
            )
            session.add(stu); session.commit()
            created += 1
        else:
            stu.name = name
            if ra: stu.ra = ra
            if is_industrial: stu.industrial_class_id = offering.id
            else:             stu.ebc_class_id = offering.id
            session.commit()
            updated += 1
    return created, updated

def _xlsx_bytes_from_df(df: pd.DataFrame) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="xlsxwriter") as xw:
        df.to_excel(xw, index=False, sheet_name="Sheet1")
    return bio.getvalue()

def make_students_template() -> bytes:
    df = pd.DataFrame([{"name": "NOME COMPLETO", "ra": "RA123456", "email": "RA123456@pucsp.edu.br"}])
    return _xlsx_bytes_from_df(df)

def make_themes_template() -> bytes:
    df = pd.DataFrame([
        {"title": "Privatização da Telebras e o impacto na concorrência e preços", "category": "Privatização", "active": True}
    ])
    return _xlsx_bytes_from_df(df)

# ---------------------------
# Abas por papel do usuário
# ---------------------------
if st.session_state.user_role == 'student':
    tabs = st.tabs(["Grupos & Temas", "Upload"])
else:
    tabs = st.tabs(["Grupos & Temas", "Upload", "Avaliação", "Relatórios", "Admin (Students)", "Temas (Gestão)", "Grupos (Admin)"])

# ---------------------------
# Tab 1 – Grupos & Temas
# ---------------------------
with tabs[0]:
    st.header("Grupos & Temas (mín. 3 para reservar; máx. 5 — 6 com autorização)")

    if st.session_state.user_role == 'student':
        membership = session.query(GroupMember).filter_by(student_id=current_user.id).first()
        if membership:
            grp = session.query(Group).get(membership.group_id)
            st.subheader(f"Seu Grupo: {grp.name}")
            st.write(f"**Tema:** {grp.theme}")
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

            # Histórico de envios do grupo (com “quem enviou”)
            st.markdown("**Submissões do Grupo:**")
            subs = session.query(Submission).filter_by(group_id=grp.id).order_by(Submission.id.desc()).all()
            if subs:
                for s in subs:
                    files = json.loads(s.files)
                    who = files.get("_by","(sem identific.)")
                    when = s.timestamp
                    st.write(f"📄 Envio em {when} por **{who}**")
                    for lbl, fn in files.items():
                        if lbl in ("_meta","_by","termo","video_file"): 
                            continue
                        p = os.path.join("uploads", f"group_{grp.id}", when, fn)
                        if os.path.exists(p):
                            with open(p, "rb") as f:
                                st.download_button(f"Baixar {lbl.capitalize()} ({fn})", data=f.read(), file_name=fn, key=f"stu_{s.id}_{lbl}")
            else:
                st.caption("_Sem envios._")

            st.info("Para enviar arquivos, acesse a aba **Upload**.")
        else:
            st.subheader("Entrar em um grupo existente")
            groups = session.query(Group).all()
            joinables = []
            for g in groups:
                n = len(g.members_assoc)
                limit = 6 if bool(getattr(g, "allow_six", False)) else 5
                if n < limit:
                    joinables.append(f"{g.name} | {g.theme} ({n}/{limit})")
            if joinables:
                choice = st.selectbox("Escolha um grupo para entrar:", [""] + joinables, index=0)
                if choice:
                    gname = choice.split(" | ")[0]
                    grp = session.query(Group).filter_by(name=gname).first()
                    if grp:
                        limit = 6 if bool(getattr(grp, "allow_six", False)) else 5
                        if len(grp.members_assoc) >= limit:
                            st.error(f"Grupo já atingiu o limite de {limit} integrantes.")
                        else:
                            session.add(GroupMember(group_id=grp.id, student_id=current_user.id))
                            session.commit()
                            st.success(f"Você entrou no {grp.name}.")
                            st.rerun()
            else:
                st.write("_Não há grupos com vagas._")

            st.markdown("---")
            st.subheader("Criar novo grupo")
            theme_records = session.query(Theme).filter(Theme.active == True).order_by(Theme.title.asc()).all()
            use_catalog = st.checkbox("Escolher tema do catálogo", value=True)
            new_theme = None
            if use_catalog and theme_records:
                titles = [t.title for t in theme_records]
                new_theme = st.selectbox("Tema (catálogo)", titles)
            else:
                new_theme = st.text_input("Tema (livre, sem duplicidade)")
            if st.button("Criar Grupo"):
                if not (new_theme or "").strip():
                    st.error("Informe um tema.")
                else:
                    same = session.query(Group).filter(Group.theme.ilike(new_theme.strip())).first()
                    if same:
                        st.error("Tema já escolhido por outro grupo.")
                    else:
                        primary_off = current_user.industrial_class or current_user.ebc_class
                        base = primary_off.name if primary_off else "GRP"
                        existing = [g for g in session.query(Group).all() if g.name.startswith(f"Grupo {base}-")]
                        next_num = len(existing) + 1
                        gname = f"Grupo {base}-{next_num}"
                        g = Group(name=gname, theme=new_theme.strip(),
                                  primary_offering_id=primary_off.id if primary_off else None)
                        session.add(g); session.commit()
                        session.add(GroupMember(group_id=g.id, student_id=current_user.id)); session.commit()
                        st.success(f"Grupo **{gname}** criado.")
                        st.rerun()
    else:
        st.write("**Todos os grupos:**")
        groups = session.query(Group).all()
        if not groups:
            st.write("_Nenhum grupo ainda._")
        for g in groups:
            names = ", ".join([gm.student.name for gm in g.members_assoc]) or "–"
            limit = 6 if bool(getattr(g, "allow_six", False)) else 5
            st.write(f"- **{g.name}** – Tema: *{g.theme}* – Integrantes: {names} (máx {limit})")

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

            # Turmas do aluno
            ind_off = current_user.industrial_class
            ebc_off = current_user.ebc_class
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
            turma_default = disc_off.name if disc_off else (ind_off.name if ind_off else (ebc_off.name if ebc_off else ""))
            turma_entrega = st.text_input("Turma da entrega (ex.: MA6, MB6, NA6, NB6)", value=turma_default)

            # Termo de cessão como CHECKBOX (sem arquivo)
            consent = st.checkbox("Cedo os direitos patrimoniais à PUC‑SP para divulgação acadêmica/extensionista, com crédito aos autores.", value=False)

            # Link de vídeo (obrigatório)
            video_url = st.text_input("Link do vídeo (YouTube/Stream/Drive) **obrigatório**").strip()

            # Arquivos obrigatórios (relatório/slides). Vídeo‑arquivo é opcional (preferir link)
            report = st.file_uploader("Relatório (pdf/docx) **obrigatório**", type=["pdf", "docx"])
            slides = st.file_uploader("Slides (pptx/pdf) **obrigatório**", type=["pptx", "pdf"])
            video_file  = st.file_uploader("Vídeo (mp4/mov/mkv/avi/mpeg) – **opcional** (prefira link)", type=["mp4","mov","mkv","avi","mpeg"])

            if st.button("Enviar"):
                if not all([consent, report, slides, video_url]):
                    st.error("Marque o **termo**, anexe **relatório** + **slides** e informe o **link do vídeo**.")
                elif not turma_entrega.strip():
                    st.error("Informe a turma da entrega.")
                else:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    base_dir = os.path.join("uploads", f"group_{grp.id}", timestamp)
                    os.makedirs(base_dir, exist_ok=True)
                    files = []
                    saved = {}

                    # salva local: relatório e slides
                    for label, up in [("relatorio", report), ("slides", slides)]:
                        path = os.path.join(base_dir, up.name)
                        with open(path, "wb") as f:
                            f.write(up.getbuffer())
                        saved[label] = up.name
                        files.append((label, up.name))

                    # salva vídeo-ARQUIVO se enviado (opcional)
                    if video_file:
                        path = os.path.join(base_dir, video_file.name)
                        with open(path, "wb") as f:
                            f.write(video_file.getbuffer())
                        saved["video_file"] = video_file.name
                        files.append(("video_file", video_file.name))

                    # Metadados (_meta) e consentimento (_by)
                    saved["_meta"] = {
                        "disciplina": disc_label,
                        "disciplina_code": disc_code,
                        "turma": turma_entrega.strip(),
                        "video_url": video_url
                    }
                    saved["termo"] = 1
                    saved["_by"] = current_user.email

                    # SharePoint (opcional)
                    sp_client_id     = st.secrets.get("sp_client_id")
                    sp_client_secret = st.secrets.get("sp_client_secret")
                    sp_tenant_id     = st.secrets.get("sp_tenant_id")
                    sp_drive_id      = st.secrets.get("sp_drive_id")
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
                        # envia cada arquivo salvo localmente (relatório/slides e, se houver, vídeo_arquivo)
                        for label, fname in files:
                            local_path = os.path.join(base_dir, fname)
                            try:
                                enc = urllib.parse.quote(fname)
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
                                st.error(f"SharePoint falhou para {fname}: {e}")

                    sub = Submission(group_id=grp.id, timestamp=timestamp, files=json.dumps(saved, ensure_ascii=False))
                    session.add(sub); session.commit()
                    st.success("Submissão registrada. Arquivos salvos localmente e (se configurado) no SharePoint.")
                    st.rerun()

# ---------------------------
# Tab 3 – Avaliação (Docente)
# ---------------------------
if st.session_state.user_role == 'teacher':
    with tabs[2]:
        st.header(f"Avaliações de {current_user.name}")
        teacher_offs = current_user.offerings
        all_groups = session.query(Group).all()
        is_admin = (getattr(current_user, "email", "").lower() == ADMIN_EMAIL.lower())

        class_opts = ["Todas"] + ([o.name for o in teacher_offs] if not is_admin else sorted({o.name for o in session.query(Offering).all()}))
        sel_class = st.selectbox("Turma", class_opts)

        if is_admin:
            disc_opts = ["Todas", "Economia Industrial", "Economia Brasileira Contemporânea II"]
            sel_disc = st.selectbox("Disciplina", disc_opts)
        else:
            discs = list({o.discipline.name for o in teacher_offs})
            sel_disc = st.selectbox("Disciplina", ["Todas"] + discs)

        # aplica filtros (docente vê todos; filtros só para navegação)
        filtered = []
        for g in all_groups:
            ok = True
            if sel_class != "Todas":
                has = any(
                    (gm.student.industrial_class and gm.student.industrial_class.name == sel_class) or
                    (gm.student.ebc_class and gm.student.ebc_class.name == sel_class)
                    for gm in g.members_assoc
                )
                ok = has
            if ok and sel_disc != "Todas":
                hasd = any(
                    (sel_disc.startswith("Economia Industrial") and gm.student.industrial_class_id) or
                    (sel_disc.startswith("Economia Brasileira") and gm.student.ebc_class_id)
                    for gm in g.members_assoc
                )
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
                names = ", ".join(sorted([gm.student.name for gm in grp.members_assoc]))
                st.caption(f"Integrantes: {names if names else '—'}")

                # histórico
                subs = session.query(Submission).filter_by(group_id=grp.id).order_by(Submission.id.desc()).all()
                if subs:
                    st.markdown("**Histórico de envios:**")
                    for s in subs:
                        files = json.loads(s.files)
                        meta = files.get("_meta", {})
                        who = files.get("_by","(sem identific.)")
                        st.write(f"📄 {s.timestamp} — {meta} — por **{who}**")
                        for lbl, fn in files.items():
                            if lbl in ("_meta","_by","termo"): continue
                            path = os.path.join("uploads", f"group_{grp.id}", s.timestamp, fn)
                            nice = {"relatorio":"Relatório", "slides":"Slides", "video_file":"Vídeo (arquivo)"}.get(lbl,lbl)
                            if os.path.exists(path):
                                with open(path, "rb") as f:
                                    data = f.read()
                                ext = fn.lower().split(".")[-1]
                                if ext in ("mp4","mov","mkv","avi","mpeg"):
                                    st.video(data)
                                elif ext in ("mp3","wav","m4a","aac","ogg"):
                                    st.audio(data)
                                else:
                                    st.download_button(f"Baixar {nice} ({fn})", data=data, file_name=fn, key=f"d_{s.id}_{lbl}")
                            else:
                                st.write(f"- {fn} (não encontrado localmente)")
                    # Link de vídeo
                    vurl = json.loads(subs[0].files).get("_meta", {}).get("video_url","")
                    if vurl:
                        st.write(f"🎬 Vídeo: {vurl}")
                        if "youtube.com" in vurl or "youtu.be" in vurl:
                            st.video(vurl)
                else:
                    st.write("_Sem envios._")

                st.markdown("---")
                st.markdown("**Avaliação**")
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

                st.markdown("**Participação (por integrante, opcional):**")
                part_inputs = {}
                for gm in grp.members_assoc:
                    lab = f"{gm.student.name}"
                    part_inputs[gm.id] = st.text_input(lab, value=gm.participation or "", key=f"p_{gm.id}")

                if st.button("Salvar avaliação"):
                    if teaches_ind:
                        grp.industrial_grade = (ind_grade or "").strip() or None
                        grp.industrial_comment = (ind_comment or "").strip() or None
                        grp.industrial_approved = bool(ind_ok)
                    if teaches_ebc:
                        grp.ebc_grade = (ebc_grade or "").strip() or None
                        grp.ebc_comment = (ebc_comment or "").strip() or None
                        grp.ebc_approved = bool(ebc_ok)
                    for gm in grp.members_assoc:
                        gm.participation = (part_inputs.get(gm.id, "") or "").strip() or None
                    session.commit()
                    st.success("Avaliação salva.")
                    st.rerun()

# ---------------------------
# Tab 4 – Relatórios (Doc.)
# ---------------------------
if st.session_state.user_role == 'teacher':
    with tabs[3]:
        st.header("Relatórios (PDF + Listagens)")
        if FPDF is None:
            st.error("A biblioteca fpdf2 não está instalada. Adicione 'fpdf2' ao requirements.txt para habilitar PDFs.")
        else:
            from io import BytesIO
            import zipfile

            col1, col2, col3 = st.columns(3)
            b1 = col1.button("PDF por Grupo (zip)")
            b2 = col2.button("PDF por Aluno (zip)")
            b3 = col3.button("PDF Resumo do Docente")

            is_admin = (getattr(current_user, "email", "").lower() == ADMIN_EMAIL.lower())

            def pdf_bytes_for_group(grp: Group):
                pdf = FPDF(); pdf.add_page()
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
                import zipfile
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
                import zipfile
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
                offs = current_user.offerings if (getattr(current_user, "email", "").lower() != ADMIN_EMAIL.lower()) else session.query(Offering).all()
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

        st.markdown("---")
        st.subheader("Listagens úteis (IND/EBC)")
        # 1) só IND, 2) só EBC, 3) ambas, 4) IND e EBC em turmas diferentes
        students = session.query(Student).all()
        only_ind = [s for s in students if s.industrial_class_id and not s.ebc_class_id]
        only_ebc = [s for s in students if s.ebc_class_id and not s.industrial_class_id]
        both = [s for s in students if s.industrial_class_id and s.ebc_class_id]
        diff = [s for s in both if (s.industrial_class.name != s.ebc_class.name)]
        def tbl(title, lst):
            st.markdown(f"**{title}** ({len(lst)})")
            if not lst: st.caption("—")
            else:
                rows = []
                for s in lst:
                    rows.append({
                        "Aluno": s.name, "E-mail": s.email, "RA": s.ra or "-",
                        "IND": s.industrial_class.name if s.industrial_class else "-",
                        "EBC": s.ebc_class.name if s.ebc_class else "-"
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
        tbl("Somente IND", only_ind)
        tbl("Somente EBC II", only_ebc)
        tbl("Em ambas (IND + EBC II)", both)
        tbl("Em IND e EBC II em turmas diferentes", diff)

# ---------------------------
# Tab 5 – Admin (Students)
# ---------------------------
if st.session_state.user_role == 'teacher':
    with tabs[4]:
        st.header("Admin (Students)")
        st.info(
            "Faça upload de um **CSV** por turma. Formato mínimo: "
            "**name, ra, email(opcional)**. Se o email vier vazio, uso **RA@pucsp.edu.br**.\n\n"
            "Selecione abaixo a **Disciplina** e a **Turma** que este arquivo representa."
        )

        disc_label_to_obj = {"Economia Industrial": disc_ind, "Economia Brasileira Contemporânea II": disc_ebc}
        sel_disc_label = st.selectbox("Disciplina deste CSV/XLSX", list(disc_label_to_obj.keys()))
        sel_disc = disc_label_to_obj[sel_disc_label]
        sel_turma = st.text_input("Turma (ex.: MA6, MB6, NA6, NB6)")

        up = st.file_uploader("CSV (colunas: name, ra, email)", type=["csv"])
        if up and st.button("Processar CSV desta turma"):
            try:
                if not sel_turma.strip():
                    st.error("Informe a turma.")
                    st.stop()
                off = session.query(Offering).filter_by(name=sel_turma.strip(), discipline_id=sel_disc.id).first()
                if not off:
                    off = Offering(name=sel_turma.strip(), discipline_id=sel_disc.id, teacher_id=None)
                    session.add(off); session.commit()
                df = pd.read_csv(up).fillna("")
                is_ind = (sel_disc.id == disc_ind.id)
                created = updated = 0
                cols = {c.lower().strip(): c for c in df.columns}
                def getc(*opts):
                    for o in opts:
                        if o in cols: return cols[o]
                    return None
                c_name = getc("name","nome","aluno")
                c_ra   = getc("ra","registro","matricula","rm","mtr")
                c_email= getc("email","e-mail")
                if not c_name or not c_ra:
                    st.error("A planilha deve conter ao menos as colunas 'name' e 'ra'.")
                else:
                    for _, row in df.iterrows():
                        name = str(row[c_name]).strip()
                        ra   = str(row[c_ra]).strip()
                        email_raw = (str(row[c_email]).strip().lower() if c_email else "")
                        if not name or not ra: continue
                        email = (email_raw if email_raw else f"{ra}@pucsp.edu.br").lower()
                        stu = session.query(Student).filter_by(email=email).first()
                        if not stu:
                            stu = Student(
                                name=name, email=email, ra=ra or None, password=(ra or "1234"),
                                industrial_class_id=off.id if is_ind else None,
                                ebc_class_id=off.id if not is_ind else None
                            )
                            session.add(stu); session.commit()
                            created += 1
                        else:
                            stu.name = name
                            if ra: stu.ra = ra
                            if is_ind: stu.industrial_class_id = off.id
                            else:      stu.ebc_class_id = off.id
                            session.commit(); updated += 1
                    st.success(f"Processado: {created} criados, {updated} atualizados para {sel_disc_label} – {sel_turma}.")
            except Exception as e:
                st.error(f"Falha ao importar CSV: {e}")

        st.markdown("---")
        st.markdown("### Templates XLSX")
        colx1, colx2 = st.columns(2)
        if colx1.download_button("Baixar template de ALUNOS (XLSX)", data=_xlsx_bytes_from_df(pd.DataFrame([{"name": "NOME", "ra": "RA123456", "email":"RA123456@pucsp.edu.br"}])), file_name="template_alunos.xlsx"):
            pass
        if colx2.download_button("Baixar template de TEMAS (XLSX)", data=_xlsx_bytes_from_df(pd.DataFrame([{"title":"Título do tema","category":"Categoria","active":True}])), file_name="template_temas.xlsx"):
            pass

        st.markdown("---")
        st.markdown("### Importar ALUNOS (XLSX)")
        up_xlsx_students = st.file_uploader("Planilha de alunos (.xlsx) — colunas: name, ra, email (opcional)", type=["xlsx"], key="xlsx_students")
        if up_xlsx_students and st.button("Processar XLSX de alunos"):
            try:
                if not sel_turma.strip():
                    st.error("Informe a turma acima antes de importar.")
                else:
                    off = session.query(Offering).filter_by(name=sel_turma.strip(), discipline_id=sel_disc.id).first()
                    if not off:
                        off = Offering(name=sel_turma.strip(), discipline_id=sel_disc.id, teacher_id=None)
                        session.add(off); session.commit()
                    is_ind = (sel_disc.id == disc_ind.id)
                    df = pd.read_excel(up_xlsx_students).fillna("")
                    created = updated = 0
                    cols = {c.lower().strip(): c for c in df.columns}
                    def getc(*opts):
                        for o in opts:
                            if o in cols: return cols[o]
                        return None
                    c_name = getc("name","nome","aluno")
                    c_ra   = getc("ra","registro","matricula","rm","mtr")
                    c_email= getc("email","e-mail")
                    if not c_name or not c_ra:
                        st.error("A planilha deve conter ao menos as colunas 'name' e 'ra'.")
                    else:
                        for _, row in df.iterrows():
                            name = str(row[c_name]).strip()
                            ra   = str(row[c_ra]).strip()
                            email_raw = (str(row[c_email]).strip().lower() if c_email else "")
                            if not name or not ra: continue
                            email = (email_raw if email_raw else f"{ra}@pucsp.edu.br").lower()
                            stu = session.query(Student).filter_by(email=email).first()
                            if not stu:
                                stu = Student(
                                    name=name, email=email, ra=ra or None, password=(ra or "1234"),
                                    industrial_class_id=off.id if is_ind else None,
                                    ebc_class_id=off.id if not is_ind else None
                                )
                                session.add(stu); session.commit()
                                created += 1
                            else:
                                stu.name = name
                                if ra: stu.ra = ra
                                if is_ind: stu.industrial_class_id = off.id
                                else:      stu.ebc_class_id = off.id
                                session.commit(); updated += 1
                        st.success(f"Alunos: {created} criado(s), {updated} atualizado(s).")
            except Exception as e:
                st.error(f"Erro ao processar XLSX de alunos: {e}")

        st.markdown("---")
        st.markdown("### Importar planilhas Excel por nome do arquivo (.xls/.xlsx)")
        st.caption("O nome do arquivo deve conter: <TURMA> <IND|EBC II> <NOME DO DOCENTE>. Ex.: '250816 NA6 IND Roland.xls'.")
        xls_files = st.file_uploader("Selecione uma ou mais planilhas", type=["xls","xlsx"], accept_multiple_files=True, key="xls_uploader")
        if xls_files and st.button("Processar planilhas (.xls/.xlsx)"):
            total_created = total_updated = 0
            log_lines = []
            for upl in xls_files:
                try:
                    turma, disc_label, docente_nome = parse_filemeta(upl.name)
                    if not turma or not disc_label:
                        log_lines.append(f"❗ {upl.name}: não consegui inferir turma/disciplina.")
                        continue
                    teacher = get_or_create_teacher(session, docente_nome or "", "")
                    off = get_or_create_offering(session, disc_label, turma, teacher)
                    is_ind = (disc_label == "Economia Industrial")
                    df = pd.read_excel(upl).fillna("")
                    c,u = import_students_df(session, df, off, is_ind)
                    total_created += c; total_updated += u
                    log_lines.append(f"✅ {upl.name}: {turma} | {disc_label} | Docente: {teacher.name} — {c} criados, {u} atualizados.")
                except Exception as e:
                    log_lines.append(f"❌ {upl.name}: erro ao processar: {e}")
            st.success(f"Concluído. Criados: {total_created}, Atualizados: {total_updated}")
            st.text("\n".join(log_lines))

        st.markdown("---")
        st.markdown("### Editar ALUNO individualmente")
        email_lookup = st.text_input("E-mail do aluno (case-insensitive)").strip().lower()
        if st.button("Carregar aluno"):
            stu = session.query(Student).filter(Student.email == email_lookup).first()
            if not stu:
                st.error("Aluno não encontrado.")
            else:
                st.session_state["_edit_stu_id"] = stu.id
        if st.session_state.get("_edit_stu_id"):
            stu = session.query(Student).get(st.session_state["_edit_stu_id"])
            new_name = st.text_input("Nome", value=stu.name)
            new_ra   = st.text_input("RA", value=stu.ra or "")
            offs_ind = session.query(Offering).filter_by(discipline_id=disc_ind.id).all()
            offs_ebc = session.query(Offering).filter_by(discipline_id=disc_ebc.id).all()
            opt_ind = ["(sem)"] + [o.name for o in offs_ind]
            opt_ebc = ["(sem)"] + [o.name for o in offs_ebc]
            idx_ind = opt_ind.index(stu.industrial_class.name) if stu.industrial_class and stu.industrial_class.name in opt_ind else 0
            idx_ebc = opt_ebc.index(stu.ebc_class.name) if stu.ebc_class and stu.ebc_class.name in opt_ebc else 0
            sel_ind = st.selectbox("Turma IND", opt_ind, index = idx_ind)
            sel_ebc = st.selectbox("Turma EBC II", opt_ebc, index = idx_ebc)
            if st.button("Salvar aluno"):
                stu.name = new_name.strip()
                stu.ra   = new_ra.strip() or None
                if sel_ind == "(sem)":
                    stu.industrial_class_id = None
                else:
                    o = session.query(Offering).filter_by(name=sel_ind, discipline_id=disc_ind.id).first()
                    if o: stu.industrial_class_id = o.id
                if sel_ebc == "(sem)":
                    stu.ebc_class_id = None
                else:
                    o = session.query(Offering).filter_by(name=sel_ebc, discipline_id=disc_ebc.id).first()
                    if o: stu.ebc_class_id = o.id
                session.commit()
                st.success("Aluno atualizado.")

# ---------------------------
# Tab 6 – Temas (Gestão)
# ---------------------------
if st.session_state.user_role == 'teacher':
    with tabs[5]:
        st.header("Gestão de Temas")
        up_themes = st.file_uploader("Importar TEMAS (XLSX) — colunas: title, category, active", type=["xlsx"])
        if up_themes and st.button("Processar XLSX de TEMAS"):
            try:
                df = pd.read_excel(up_themes).fillna("")
                cols = {c.lower().strip(): c for c in df.columns}
                c_title = cols.get("title"); c_cat = cols.get("category"); c_active = cols.get("active")
                if not c_title:
                    st.error("A planilha precisa ter a coluna 'title'.")
                else:
                    added = updated = 0
                    for _, row in df.iterrows():
                        title = str(row[c_title]).strip()
                        if not title: continue
                        cat   = str(row[c_cat]).strip() if c_cat else None
                        act   = row[c_active] if c_active else True
                        if isinstance(act, str): act = act.strip().lower() in ("1","true","sim","yes","y")
                        t = session.query(Theme).filter(Theme.title.ilike(title)).first()
                        if not t:
                            t = Theme(title=title, category=cat or None, active=bool(act))
                            session.add(t); session.commit(); added += 1
                        else:
                            t.category = cat or None
                            t.active   = bool(act)
                            session.commit(); updated += 1
                    st.success(f"Temas: {added} adicionados, {updated} atualizados.")
            except Exception as e:
                st.error(f"Erro ao processar XLSX de temas: {e}")

        st.markdown("---")
        st.subheader("Temas cadastrados")
        q = session.query(Theme).order_by(Theme.active.desc(), Theme.title.asc()).all()
        if not q:
            st.info("Nenhum tema cadastrado.")
        else:
            for t in q:
                with st.expander(f"{'✅' if t.active else '🚫'} {t.title}"):
                    new_title = st.text_input("Título", value=t.title, key=f"tt_{t.id}")
                    new_cat   = st.text_input("Categoria", value=t.category or "", key=f"tc_{t.id}")
                    new_act   = st.checkbox("Ativo", value=bool(t.active), key=f"ta_{t.id}")
                    colu, cold = st.columns(2)
                    if colu.button("Salvar", key=f"tup_{t.id}"):
                        other = session.query(Theme).filter(Theme.title.ilike(new_title), Theme.id != t.id).first()
                        if other:
                            st.error("Já existe outro tema com esse título.")
                        else:
                            t.title = new_title.strip()
                            t.category = new_cat.strip() or None
                            t.active = bool(new_act)
                            session.commit()
                            st.success("Tema atualizado.")
                    if cold.button("Excluir", key=f"tdel_{t.id}"):
                        session.delete(t); session.commit()
                        st.warning("Tema excluído.")
                        st.rerun()

# ---------------------------
# Tab 7 – Grupos (Admin)
# ---------------------------
if st.session_state.user_role == 'teacher':
    with tabs[6]:
        st.header("Admin – Grupos (Permitir 6 / Publicação)")
        groups = session.query(Group).order_by(Group.name.asc()).all()
        for g in groups:
            with st.expander(f"{g.name} — {g.theme}"):
                col1, col2, col3 = st.columns([1,1,2])
                allow6 = col1.checkbox("Permitir 6 alunos", value=bool(getattr(g, "allow_six", False)), key=f"allow6_{g.id}")
                publish = col2.checkbox("Publicar na galeria", value=bool(getattr(g, "publish_public", False)), key=f"pub_{g.id}")
                # checa tamanho atual x limite
                n = len(g.members_assoc)
                limit = 6 if allow6 else 5
                col3.caption(f"Integrantes: {n} / {limit} (mín. para reservar: 3)")
                if col3.button("Salvar", key=f"savegrp_{g.id}"):
                    g.allow_six = bool(allow6)
                    g.publish_public = bool(publish)
                    session.commit()
                    st.success("Atualizado.")
                    st.rerun()

st.caption("MVP – Submissões Industrial & EBC II (2025/2)")
