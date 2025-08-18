*** a/app.py
--- b/app.py
***************
*** 1,15 ****
  import streamlit as st
  from datetime import datetime
  import os, json, urllib
  import requests

  try:
      from fpdf import FPDF
  except ImportError:
      FPDF = None

  from sqlalchemy import (
      create_engine, Column, Integer, String, Boolean, ForeignKey, Text
  )
  from sqlalchemy.orm import sessionmaker, relationship, declarative_base
--- 1,16 ----
  import streamlit as st
  from datetime import datetime
  import os, json, urllib
  import requests
+ from typing import Optional

  try:
      from fpdf import FPDF
  except ImportError:
      FPDF = None

  from sqlalchemy import (
      create_engine, Column, Integer, String, Boolean, ForeignKey, Text
  )
  from sqlalchemy.orm import sessionmaker, relationship, declarative_base
***************
*** 33,53 ****
  class Student(Base):
      __tablename__ = 'students'
      id = Column(Integer, primary_key=True, autoincrement=True)
      ra = Column(String)  # RA
      name = Column(String, nullable=False)
!     email = Column(String, unique=True, nullable=False)     # armazenado em minúsculas
      password = Column(String, nullable=False)               # (em produção, usar hash)
      industrial_class_id = Column(Integer, ForeignKey('offerings.id'))
      ebc_class_id = Column(Integer, ForeignKey('offerings.id'))
      industrial_class = relationship("Offering", foreign_keys=[industrial_class_id])
      ebc_class = relationship("Offering", foreign_keys=[ebc_class_id])
!     groups_assoc = relationship("GroupMember", back_populates="student")
!     groups = relationship("Group", secondary="group_members", back_populates="members")

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
!     members_assoc = relationship("GroupMember", back_populates="group")
!     members = relationship("Student", secondary="group_members", back_populates="groups")

  class GroupMember(Base):
      __tablename__ = 'group_members'
      id = Column(Integer, primary_key=True, autoincrement=True)
      group_id = Column(Integer, ForeignKey('groups.id'), nullable=False)
      student_id = Column(Integer, ForeignKey('students.id'), nullable=False)
      participation = Column(Text)
!     group = relationship("Group", back_populates="members_assoc")
!     student = relationship("Student", back_populates="groups_assoc")
--- 34,61 ----
  class Student(Base):
      __tablename__ = 'students'
      id = Column(Integer, primary_key=True, autoincrement=True)
      ra = Column(String)  # RA
      name = Column(String, nullable=False)
!     email = Column(String, unique=True, nullable=False)     # será salvo em minúsculas
      password = Column(String, nullable=False)               # (em produção, usar hash)
      industrial_class_id = Column(Integer, ForeignKey('offerings.id'))
      ebc_class_id = Column(Integer, ForeignKey('offerings.id'))
      industrial_class = relationship("Offering", foreign_keys=[industrial_class_id])
      ebc_class = relationship("Offering", foreign_keys=[ebc_class_id])
!     groups_assoc = relationship("GroupMember", back_populates="student")
!     groups = relationship(
!         "Group",
!         secondary="group_members",
!         back_populates="members",
!         overlaps="groups_assoc"
!     )

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
!     members_assoc = relationship(
!         "GroupMember",
!         back_populates="group",
!         overlaps="members"
!     )
!     members = relationship(
!         "Student",
!         secondary="group_members",
!         back_populates="groups",
!         overlaps="members_assoc,groups_assoc"
!     )

  class GroupMember(Base):
      __tablename__ = 'group_members'
      id = Column(Integer, primary_key=True, autoincrement=True)
      group_id = Column(Integer, ForeignKey('groups.id'), nullable=False)
      student_id = Column(Integer, ForeignKey('students.id'), nullable=False)
      participation = Column(Text)
!     group = relationship(
!         "Group",
!         back_populates="members_assoc",
!         overlaps="members,groups"
!     )
!     student = relationship(
!         "Student",
!         back_populates="groups_assoc",
!         overlaps="groups,members"
!     )
***************
*** 74,86 ****
  ADMIN_EMAIL = (st.secrets.get("ADMIN_EMAIL", "rsaldanha@pucsp.br") or "").lower()
  ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "8722")
  DEV_QUICK_LOGIN = bool(st.secrets.get("DEV_QUICK_LOGIN", False))

  def ensure_admin():
      admin = session.query(Teacher).filter(Teacher.email == ADMIN_EMAIL).first()
      if not admin:
!         admin = Teacher(name="Administrador", email=ADMIN_EMAIL, password=ADMIN_PASSWORD)
          session.add(admin)
          session.commit()

  ensure_admin()
--- 82,96 ----
  ADMIN_EMAIL = (st.secrets.get("ADMIN_EMAIL", "rsaldanha@pucsp.br") or "").lower()
  ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "8722")
  DEV_QUICK_LOGIN = bool(st.secrets.get("DEV_QUICK_LOGIN", False))

  def ensure_admin():
      admin = session.query(Teacher).filter(Teacher.email == ADMIN_EMAIL).first()
      if not admin:
!         admin = Teacher(
!             name="Administrador",
!             email=ADMIN_EMAIL.lower(),
!             password=ADMIN_PASSWORD
!         )
          session.add(admin)
          session.commit()

  ensure_admin()
***************
*** 106,114 ****
  if st.session_state.user_id is None and DEV_QUICK_LOGIN:
      admin_user = session.query(Teacher).filter(Teacher.email == ADMIN_EMAIL).first()
      if admin_user:
          st.session_state.user_id = admin_user.id
          st.session_state.user_role = 'teacher'
          st.session_state.user_name = admin_user.name
          st.info(f"Login automático como admin ({ADMIN_EMAIL}) ativado (DEV_QUICK_LOGIN).")
-         st.experimental_rerun()
+         st.rerun()

  # Login
  if st.session_state.user_id is None:
      st.title("Login – Submissões (Industrial & EBC II)")
--- 116,127 ----
***************
*** 116,137 ****
      login_email = st.text_input("E-mail institucional").strip().lower()
      login_pass  = st.text_input("Senha", type="password")
      if st.button("Entrar"):
!         user = session.query(Teacher).filter_by(email=login_email, password=login_pass).first()
          role = 'teacher'
          if user is None:
!             user = session.query(Student).filter_by(email=login_email, password=login_pass).first()
              role = 'student'
          if user:
              st.session_state.user_id = user.id
              st.session_state.user_role = role
              st.session_state.user_name = user.name
-             st.experimental_rerun()
+             st.rerun()
          else:
              st.error("Credenciais inválidas.")
      st.stop()
--- 129,154 ----
      login_email = st.text_input("E-mail institucional").strip().lower()
      login_pass  = st.text_input("Senha", type="password")
      if st.button("Entrar"):
!         # case-insensitive por salvar sempre em minúsculas
!         user = session.query(Teacher).filter(
!             Teacher.email == login_email,
!             Teacher.password == login_pass
!         ).first()
          role = 'teacher'
          if user is None:
!             user = session.query(Student).filter(
!                 Student.email == login_email,
!                 Student.password == login_pass
!             ).first()
              role = 'student'
          if user:
              st.session_state.user_id = user.id
              st.session_state.user_role = role
              st.session_state.user_name = user.name
+             # Streamlit moderno
+             st.rerun()
          else:
              st.error("Credenciais inválidas.")
      st.stop()
***************
*** 217,223 ****
                          session.add(g); session.commit()
                          session.add(GroupMember(group_id=g.id, student_id=current_user.id)); session.commit()
                          st.success(f"Grupo **{gname}** criado.")
-                         st.experimental_rerun()
+                         st.rerun()
      else:
          st.write("**Todos os grupos:**")
          groups = session.query(Group).all()
--- 234,239 ----
***************
*** 263,269 ****
                     session.add(sub); session.commit()
                     st.success("Submissão registrada. Arquivos salvos localmente e (se configurado) no SharePoint.")
--- 279,282 ----
***************
*** 349,355 ****
                      for gm in grp.members_assoc:
                          gm.participation = part_inputs.get(gm.id, "").strip() or None
                      session.commit()
                      st.success("Avaliação salva.")
--- 362,369 ----
                      for gm in grp.members_assoc:
                          gm.participation = part_inputs.get(gm.id, "").strip() or None
                      session.commit()
                      st.success("Avaliação salva.")
+                     st.rerun()
***************
*** 493,500 ****
          sel_turma = st.text_input("Turma (ex.: MA6, MB6, NA6, NB6)")

          up = st.file_uploader("CSV (colunas: name,ra[,email])", type=["csv"])

          if up and st.button("Processar CSV desta turma"):
              try:
                  import pandas as pd
                  if not sel_turma.strip():
                      st.error("Informe a turma.")
                      st.stop()
--- 507,517 ----
          sel_turma = st.text_input("Turma (ex.: MA6, MB6, NA6, NB6)")

          up = st.file_uploader("CSV (colunas: name,ra[,email])", type=["csv"])

          if up and st.button("Processar CSV desta turma"):
              try:
                  import pandas as pd
+                 from io import StringIO
                  if not sel_turma.strip():
                      st.error("Informe a turma.")
                      st.stop()
***************
*** 510,542 ****
                  created = updated = 0
                  for _, row in df.iterrows():
                      name = str(row.get("name","")).strip()
                      ra   = str(row.get("ra","")).strip()
                      email_raw = str(row.get("email","")).strip().lower()
                      if not name or not ra:
                          continue
!                     email = email_raw if email_raw else f"{ra}@pucsp.edu.br"

                      # normaliza para minúsculas
!                     email = email.lower()

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
--- 527,563 ----
                  created = updated = 0
                  for _, row in df.iterrows():
                      name = str(row.get("name","")).strip()
                      ra   = str(row.get("ra","")).strip()
                      email_raw = str(row.get("email","")).strip().lower()
                      if not name or not ra:
                          continue
!                     email = (email_raw if email_raw else f"{ra}@pucsp.edu.br").lower()

                      # normaliza para minúsculas
!                     email = email.lower()

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
