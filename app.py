--- a/app.py
+++ b/app.py
@@
-import streamlit as st
-import pandas as pd
-import json, os
-from datetime import datetime
-from sqlalchemy import create_engine, text
+import streamlit as st
+import pandas as pd
+import json, os, re, pathlib
+from datetime import datetime
+from sqlalchemy import create_engine, text
+
+# módulo novo: importador TXT PUC
+from app.modules.import_txt import parse_puc_txt, upsert_students_and_enroll
@@
-st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")
+st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")
@@
-DB_URL = f"sqlite:///{os.path.join(DATA_DIR,'app.db')}"
+DB_URL = f"sqlite:///{os.path.join(DATA_DIR,'app.db')}"
 engine = create_engine(DB_URL, future=True)
@@
-with engine.begin() as conn:
+with engine.begin() as conn:
     conn.exec_driver_sql("""
     CREATE TABLE IF NOT EXISTS groups(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         code TEXT UNIQUE,
-        turma TEXT CHECK (turma IN ('MA6','MB6','NA6','NB6')),
+        turma TEXT CHECK (turma IN ('MA6','MB6','NA6','NB6')),
+        -- disciplina do grupo (IND/EBCII/JOINT)
+        course_code TEXT DEFAULT 'JOINT',
         created_by TEXT,
         created_at TEXT
     );""")
@@
     conn.exec_driver_sql("""
     CREATE TABLE IF NOT EXISTS students(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         ra TEXT UNIQUE,
         name TEXT NOT NULL,
         email TEXT,
-        turma TEXT
+        turma TEXT,
+        active INTEGER DEFAULT 1
     );""")
@@
-    conn.exec_driver_sql("""
-    CREATE TABLE IF NOT EXISTS professors(
-        id INTEGER PRIMARY KEY AUTOINCREMENT,
-        name TEXT NOT NULL,
-        email TEXT UNIQUE NOT NULL,
-        role TEXT CHECK (role IN ('admin','docente')) DEFAULT 'docente',
-        pin TEXT
-    );""")
+    conn.exec_driver_sql("""
+    CREATE TABLE IF NOT EXISTS professors(
+        id INTEGER PRIMARY KEY AUTOINCREMENT,
+        name TEXT NOT NULL,
+        email TEXT UNIQUE NOT NULL,
+        role TEXT CHECK (role IN ('admin','docente')) DEFAULT 'docente',
+        pin TEXT
+    );""")
@@
-    conn.exec_driver_sql("""
-    CREATE TABLE IF NOT EXISTS ratings(
-        id INTEGER PRIMARY KEY AUTOINCREMENT,
-        submission_id INTEGER NOT NULL,
-        professor_email TEXT NOT NULL,
-        liked INTEGER DEFAULT 0,
-        score INTEGER CHECK(score BETWEEN 0 AND 10),
-        comment TEXT,
-        created_at TEXT,
-        UNIQUE(submission_id, professor_email),
-        FOREIGN KEY(submission_id) REFERENCES submissions(id)
-    );""")
+    -- substituído por 'reviews' (docentes)
+    """)
+
+# novas tabelas do semestre (idempotente)
+with engine.begin() as conn:
+    conn.exec_driver_sql("""
+    CREATE TABLE IF NOT EXISTS disciplines(
+      id INTEGER PRIMARY KEY AUTOINCREMENT,
+      code TEXT UNIQUE, name TEXT NOT NULL
+    );""")
+    conn.exec_driver_sql("""
+    CREATE TABLE IF NOT EXISTS semesters(
+      id INTEGER PRIMARY KEY AUTOINCREMENT,
+      term TEXT UNIQUE NOT NULL
+    );""")
+    conn.exec_driver_sql("""
+    CREATE TABLE IF NOT EXISTS offerings(
+      id INTEGER PRIMARY KEY AUTOINCREMENT,
+      discipline_id INTEGER NOT NULL,
+      term TEXT NOT NULL,
+      turma TEXT NOT NULL,
+      instructor_id INTEGER,
+      UNIQUE(discipline_id, term, turma)
+    );""")
+    conn.exec_driver_sql("""
+    CREATE TABLE IF NOT EXISTS enrollments(
+      id INTEGER PRIMARY KEY AUTOINCREMENT,
+      student_id INTEGER NOT NULL,
+      offering_id INTEGER NOT NULL,
+      active INTEGER DEFAULT 1,
+      UNIQUE(student_id, offering_id)
+    );""")
+    conn.exec_driver_sql("""
+    CREATE TABLE IF NOT EXISTS reviews(
+      id INTEGER PRIMARY KEY AUTOINCREMENT,
+      submission_id INTEGER NOT NULL,
+      instructor_id INTEGER NOT NULL,
+      score REAL,
+      liked INTEGER DEFAULT 0,
+      created_at TEXT,
+      UNIQUE(submission_id, instructor_id)
+    );""")
+    # seeds
+    conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('IND','Economia Industrial')"))
+    conn.execute(text("INSERT OR IGNORE INTO disciplines(code,name) VALUES('EBCII','Economia Brasileira II')"))
+    conn.execute(text("INSERT OR IGNORE INTO semesters(term) VALUES('2025/2')"))
@@
 def list_groups():
     return get_df("SELECT id, code, turma FROM groups ORDER BY turma, code")
@@
 def groups_with_counts_df():
-    df = get_df("""SELECT g.id, g.code, g.turma,
+    df = get_df("""SELECT g.id, g.code, g.turma, g.course_code,
                    COUNT(gm.student_name) AS membros
                    FROM groups g
                    LEFT JOIN group_members gm ON gm.group_id=g.id
                    GROUP BY g.id,g.code,g.turma
-                   ORDER BY g.turma,g.code""")
+                   ORDER BY g.turma,g.code""")
     if not df.empty:
         df["status_grupo"] = df["membros"].apply(lambda k: "OK" if 5<=k<=6 else "⚠️")
     return df
@@
-st.title("Submissões – Industrial & EBC II (2º/2025)")
+st.title("Submissões – Industrial & EBC II (2º/2025)")
@@
-    st.subheader("Criar grupo (5–6 alunos)")
+    st.subheader("Criar grupo (5–6 alunos)")
     c1, c2 = st.columns(2)
     with c1:
         turma = st.selectbox("Turma", ["MA6","MB6","NA6","NB6"])
         code = st.text_input("Código do grupo (ex.: MA6G1)")
     with c2:
         created_by = st.text_input("Seu nome")
+    c3, c4 = st.columns(2)
+    with c3:
+        disc = st.selectbox("Disciplina do grupo", ["JOINT","IND","EBCII"])
+    with c4:
+        st.caption("JOINT = grupo conjunto (IND + EBC II)")
-        if st.button("Criar grupo"):
+        if st.button("Criar grupo"):
             if not code or not turma:
                 st.error("Informe turma e código.")
             elif not code.upper().startswith(turma):
                 st.error("O código deve iniciar pela turma (ex.: MA6G1).")
             else:
                 try:
-                    exec_sql("""INSERT INTO groups(code,turma,created_by,created_at)
-                                VALUES(:c,:t,:u,:ts)""",
-                             c=code.strip().upper(), t=turma, u=created_by.strip(),
+                    exec_sql("""INSERT INTO groups(code,turma,course_code,created_by,created_at)
+                                VALUES(:c,:t,:cc,:u,:ts)""",
+                             c=code.strip().upper(), t=turma, cc=disc, u=created_by.strip(),
                              ts=datetime.now().isoformat(timespec="seconds"))
                     st.success("Grupo criado.")
                 except Exception as e:
                     st.error(f"Erro ao criar: {e}")
@@
-    st.subheader("Temas")
+    st.subheader("Temas")
     tipo = st.selectbox("Filtrar por tipo", ["Todos","caso","transversal","livre"])
     st.dataframe(themes_view(tipo), use_container_width=True)
@@
-    st.subheader("Reservar tema")
+    st.subheader("Reservar tema")
     if not gdf.empty:
         sg = st.selectbox("Grupo", gdf["code"].tolist(), key="reserve_g")
+        # validação: grupo precisa ter 5–6 membros
+        members2 = group_details(sg)
+        if len(members2) < 5 or len(members2) > 6:
+            st.warning("Grupo precisa ter entre 5 e 6 membros para reservar.")
         t_free = get_df("SELECT title FROM themes WHERE status='livre' ORDER BY number")["title"].tolist()
         tt = st.selectbox("Tema disponível", t_free)
         if st.button("Reservar"):
             ok, msg = reserve_theme(tt, sg)
             st.success(msg) if ok else st.error(msg)
@@
-    st.subheader("Grupos com contagem de membros")
-    st.dataframe(groups_with_counts_df(), use_container_width=True)
+    st.subheader("Grupos com contagem de membros")
+    st.dataframe(groups_with_counts_df(), use_container_width=True)
@@
-with tabs[2]:
-    st.subheader("Exportar aprovados")
+with tabs[2]:
+    st.subheader("Exportar aprovados")
@@
-    st.subheader("Avaliação por docentes")
-    email = st.text_input("E-mail institucional")
-    pin = st.text_input("PIN", type="password")
-    ok = False
-    if st.button("Entrar"):
-        dfp = get_df("SELECT * FROM professors WHERE email=:e AND pin=:p", e=email, p=pin)
-        ok = not dfp.empty
-        st.success("Acesso ok.") if ok else st.error("Credenciais inválidas.")
-    if ok:
-        sdf = get_df("""SELECT s.id, s.group_code, s.theme_title, s.submitted_at,
-                        COALESCE(r.score,'') AS sua_nota, COALESCE(r.liked,0) AS seu_like
-                        FROM submissions s LEFT JOIN ratings r
-                        ON r.submission_id=s.id AND r.professor_email=:e
-                        WHERE s.approved=1 ORDER BY s.submitted_at DESC""", e=email)
-        st.dataframe(sdf, use_container_width=True)
-        sid = st.selectbox("Trabalho (ID)", sdf["id"].tolist() if not sdf.empty else [])
-        like = st.toggle("Curtir")
-        score = st.slider("Nota", 0, 10, 8)
-        comment = st.text_area("Comentário")
-        if st.button("Salvar avaliação"):
-            exec_sql("""INSERT INTO ratings(submission_id,professor_email,liked,score,comment,created_at)
-                        VALUES(:i,:e,:l,:s,:c,:ts)
-                        ON CONFLICT(submission_id, professor_email) DO UPDATE
-                        SET liked=:l, score=:s, comment=:c, created_at=:ts""",
-                     i=int(sid), e=email, l=int(like), s=int(score), c=comment,
-                     ts=datetime.now().isoformat(timespec="seconds"))
-            st.success("Ok.")
+    st.subheader("Avaliação por docentes")
+    email = st.text_input("E-mail institucional")
+    pin = st.text_input("PIN", type="password")
+    ok = False
+    if st.button("Entrar"):
+        dfp = get_df("SELECT * FROM professors WHERE email=:e AND pin=:p", e=email, p=pin)
+        ok = not dfp.empty
+        st.success("Acesso ok.") if ok else st.error("Credenciais inválidas.")
+    if ok:
+        # mapeia professor -> instructor (auto-provision simplificado)
+        pr = get_df("SELECT id,name FROM professors WHERE email=:e", e=email)
+        instr_id = pr["id"].iloc[0]
+        sdf = get_df("""SELECT s.id, s.group_code, s.theme_title, s.submitted_at
+                        FROM submissions s
+                        WHERE s.approved=1 ORDER BY s.submitted_at DESC""")
+        st.dataframe(sdf, use_container_width=True)
+        sid = st.selectbox("Trabalho (ID)", sdf["id"].tolist() if not sdf.empty else [])
+        like = st.toggle("Curtir")
+        score = st.slider("Nota", 0, 10, 8)
+        comment = st.text_area("Comentário (opcional)")
+        if st.button("Salvar avaliação"):
+            exec_sql("""INSERT INTO reviews(submission_id,instructor_id,liked,score,created_at)
+                        VALUES(:i,:p,:l,:s,:ts)
+                        ON CONFLICT(submission_id, instructor_id) DO UPDATE
+                        SET liked=:l, score=:s, created_at=:ts""",
+                     i=int(sid), p=int(instr_id), l=int(like), s=float(score),
+                     ts=datetime.now().isoformat(timespec="seconds"))
+            st.success("Ok.")
@@
-with tabs[3]:
-    st.subheader("Aprovar submissões")
+with tabs[3]:
+    st.subheader("Aprovar submissões")
@@
-    st.subheader("Métricas")
-    m = get_df("""SELECT s.id, s.group_code, s.theme_title,
-                  AVG(r.score) AS media, SUM(r.liked) AS likes
-                  FROM submissions s LEFT JOIN ratings r ON r.submission_id=s.id
-                  GROUP BY s.id ORDER BY likes DESC, media DESC""")
+    st.subheader("Métricas")
+    m = get_df("""SELECT s.id, s.group_code, s.theme_title,
+                  ROUND(AVG(rv.score),2) AS media, SUM(rv.liked) AS likes
+                  FROM submissions s LEFT JOIN reviews rv ON rv.submission_id=s.id
+                  GROUP BY s.id ORDER BY likes DESC, media DESC""")
     st.dataframe(m, use_container_width=True)
@@
-with tabs[4]:
-    st.subheader("Importar alunos (CSV) — colunas: ra,name,email,turma")
+with tabs[4]:
+    st.subheader("Importar alunos (CSV) — colunas: ra,name,email,turma")
@@
     if up_alunos:
         df = pd.read_csv(up_alunos)
         with engine.begin() as conn:
             for row in df.to_dict(orient="records"):
                 conn.execute(text("""INSERT OR IGNORE INTO students(ra,name,email,turma)
                                   VALUES(:ra,:name,:email,:turma)"""), row)
         st.success(f"{len(df)} aluno(s) processados).")
+
+    st.subheader("Importar listas PUC (TXT) — múltiplos arquivos")
+    up_txt = st.file_uploader("Arquivos .txt", type=["txt"], accept_multiple_files=True)
+    term = st.text_input("Semestre (term)", value="2025/2")
+    disc_map = {"ECONOMIA INDUSTRIAL":"IND", "EBC II":"EBCII"}
+    if up_txt and st.button("Processar TXT"):
+        ok_count = 0
+        temp_dir = pathlib.Path("data/_tmp"); temp_dir.mkdir(parents=True, exist_ok=True)
+        for upl in up_txt:
+            fp = temp_dir / upl.name
+            fp.write_bytes(upl.read())
+            meta = parse_puc_txt(str(fp))
+            turma_txt = (meta["turma"] or "").replace("ECO-","")
+            disc_code = disc_map.get(meta["disciplina"], "IND")
+            if not turma_txt:
+                st.warning(f"{upl.name}: turma não detectada; ignorado.")
+                continue
+            upsert_students_and_enroll(engine, term, disc_code, turma_txt, meta["students"])
+            ok_count += 1
+        st.success(f"TXT processados: {ok_count}")
@@
     st.subheader("Alunos sem grupo")
     st.dataframe(students_unassigned(), use_container_width=True)
     sid = st.text_input("ID do aluno para alocar")
     gcode = st.text_input("Grupo destino (ex.: MA6G1)")
     if st.button("Alocar"):
         try:
             link_student_to_group(int(sid), gcode.strip().upper())
             st.success("Aluno alocado.")
         except Exception as e:
             st.error(str(e))
@@
     st.subheader("Gerir docentes")
     name = st.text_input("Nome")
     email = st.text_input("E-mail")
     role = st.selectbox("Papel", ["docente","admin"])
     pinp = st.text_input("PIN", type="password")
     if st.button("Salvar docente"):
         exec_sql("""INSERT INTO professors(name,email,role,pin) VALUES(:n,:e,:r,:p)
                     ON CONFLICT(email) DO UPDATE SET name=:n, role=:r, pin=:p""",
                  n=name,e=email,r=role,p=pinp)
         st.success("Docente salvo/atualizado.")
