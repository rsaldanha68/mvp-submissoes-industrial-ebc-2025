import streamlit as st
import pandas as pd
import json, os
from datetime import datetime
from sqlalchemy import create_engine, text

st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

DATA_DIR = "data"
UPLOAD_DIR = "uploads"
PUBLIC_DIR = "public"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PUBLIC_DIR, exist_ok=True)

DB_URL = f"sqlite:///{os.path.join(DATA_DIR,'app.db')}"
engine = create_engine(DB_URL, future=True)

def get_df(sql, **params):
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params)

def exec_sql(sql, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)

with engine.begin() as conn:
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS groups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        turma TEXT CHECK (turma IN ('MA6','MB6','NA6','NB6')),
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
        title TEXT UNIQUE,
        number INTEGER,
        type TEXT DEFAULT 'caso',
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
        turma TEXT
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS group_enrollments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        group_id INTEGER NOT NULL,
        UNIQUE(student_id),
        FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
        FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE
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
    CREATE TABLE IF NOT EXISTS ratings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL,
        professor_email TEXT NOT NULL,
        liked INTEGER DEFAULT 0,
        score INTEGER CHECK(score BETWEEN 0 AND 10),
        comment TEXT,
        created_at TEXT,
        UNIQUE(submission_id, professor_email),
        FOREIGN KEY(submission_id) REFERENCES submissions(id)
    );""")

def seed_themes_from_file():
    if not os.path.exists("themes.json"): return
    with open("themes.json","r",encoding="utf-8") as f:
        raw = json.load(f)
    items = []
    if isinstance(raw, list):
        for x in raw:
            if isinstance(x, str):
                items.append({"title": x, "type": "caso"})
            elif isinstance(x, dict) and "title" in x:
                items.append({"title": x["title"], "type": x.get("type","caso")})
    with engine.begin() as conn:
        have = set(pd.read_sql("SELECT title FROM themes", conn)["title"].tolist())
        num = pd.read_sql("SELECT MAX(COALESCE(number,0)) AS mx FROM themes", conn)["mx"].iloc[0] or 0
        for it in items:
            if it["title"] in have: continue
            num += 1
            conn.execute(text("""
                INSERT INTO themes(title,number,type,status) VALUES(:t,:n,:tp,'livre')
            """), {"t": it["title"], "n": int(num), "tp": it["type"]})
seed_themes_from_file()

def list_groups():
    return get_df("SELECT id, code, turma FROM groups ORDER BY turma, code")

def group_details(code):
    dfm = get_df("""SELECT student_name FROM group_members gm
                    JOIN groups g ON gm.group_id=g.id
                    WHERE g.code=:c""", c=code)
    return dfm["student_name"].tolist() if not dfm.empty else []

def groups_with_counts_df():
    df = get_df("""SELECT g.id, g.code, g.turma,
                   COUNT(gm.student_name) AS membros
                   FROM groups g
                   LEFT JOIN group_members gm ON gm.group_id=g.id
                   GROUP BY g.id,g.code,g.turma
                   ORDER BY g.turma,g.code""")
    if not df.empty:
        df["status_grupo"] = df["membros"].apply(lambda k: "OK" if 5<=k<=6 else "⚠️")
    return df

def students_unassigned():
    return get_df("""SELECT s.id, s.ra, s.name, s.email, s.turma
                     FROM students s
                     LEFT JOIN group_enrollments ge ON ge.student_id=s.id
                     WHERE ge.id IS NULL
                     ORDER BY s.turma, s.name""")

def link_student_to_group(student_id:int, group_code:str):
    gdf = get_df("SELECT id FROM groups WHERE code=:c", c=group_code)
    if gdf.empty: raise ValueError("Grupo inexistente")
    gid = int(gdf["id"].iloc[0])
    exec_sql("INSERT OR REPLACE INTO group_enrollments(student_id,group_id) VALUES(:s,:g)", s=int(student_id), g=gid)

def reserve_theme(theme_title, group_code):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM themes WHERE title=:t"), {"t": theme_title}).fetchone()
        if not row or row[0] != "livre": return False, "Tema já reservado."
        conn.execute(text("""
            UPDATE themes SET status='reservado', reserved_by=:g, reserved_at=:ts,
                              released_by=NULL, released_at=NULL
            WHERE title=:t
        """), {"g": group_code, "t": theme_title, "ts": datetime.now().isoformat(timespec="seconds")})
    return True, "Reservado com sucesso."

def release_theme(theme_title):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM themes WHERE title=:t"), {"t": theme_title}).fetchone()
        if not row or row[0] != "reservado": return False, "Tema não está reservado."
        conn.execute(text("""
            UPDATE themes SET status='livre', reserved_by=NULL, reserved_at=NULL,
                              released_by='aluno', released_at=:ts
            WHERE title=:t
        """), {"t": theme_title, "ts": datetime.now().isoformat(timespec="seconds")})
    return True, "Tema liberado."

def themes_view(tipo_filter=None):
    q = """SELECT number AS "Nº", title AS Tema, type AS Tipo, status AS Status,
           reserved_by AS Grupo, reserved_at AS Reservado_em FROM themes"""
    if tipo_filter and tipo_filter != "Todos":
        return get_df(q + " WHERE type=:tp ORDER BY number", tp=tipo_filter)
    return get_df(q + " ORDER BY number")

st.title("Submissões – Industrial & EBC II (2º/2025)")

tabs = st.tabs([
    "1) Grupos & Temas",
    "2) Upload",
    "3) Galeria / Avaliação",
    "4) Administração",
    "5) Alunos & Docentes"
])

with tabs[0]:
    st.subheader("Criar grupo (5–6 alunos)")
    c1, c2 = st.columns(2)
    with c1:
        turma = st.selectbox("Turma", ["MA6","MB6","NA6","NB6"])
        code = st.text_input("Código do grupo (ex.: MA6G1)")
    with c2:
        created_by = st.text_input("Seu nome")
        if st.button("Criar grupo"):
            if not code or not turma:
                st.error("Informe turma e código.")
            elif not code.upper().startswith(turma):
                st.error("O código deve iniciar pela turma (ex.: MA6G1).")
            else:
                try:
                    exec_sql("""INSERT INTO groups(code,turma,created_by,created_at)
                                VALUES(:c,:t,:u,:ts)""",
                             c=code.strip().upper(), t=turma, u=created_by.strip(),
                             ts=datetime.now().isoformat(timespec="seconds"))
                    st.success("Grupo criado.")
                except Exception as e:
                    st.error(f"Erro ao criar: {e}")

    st.markdown("—")
    st.subheader("Membros")
    gdf = list_groups()
    if gdf.empty:
        st.info("Crie um grupo primeiro.")
    else:
        sel_group = st.selectbox("Grupo", gdf["code"].tolist())
        members = group_details(sel_group)
        new_member = st.text_input("Adicionar membro")
        c3, c4 = st.columns(2)
        if c3.button("Adicionar"):
            gid = gdf[gdf["code"] == sel_group]["id"].iloc[0]
            try:
                exec_sql("INSERT INTO group_members(group_id,student_name) VALUES(:g,:n)",
                         g=int(gid), n=new_member.strip())
                st.success("Adicionado.")
            except Exception as e:
                st.error(f"Erro: {e}")
        if c4.button("Remover último"):
            gid = gdf[gdf["code"] == sel_group]["id"].iloc[0]
            exec_sql("""DELETE FROM group_members WHERE rowid IN (
                        SELECT rowid FROM group_members WHERE group_id=:g
                        ORDER BY rowid DESC LIMIT 1)""", g=int(gid))
            st.warning("Removido.")
        st.write("Membros:", members, f"({len(members)}/6)")

    st.markdown("—")
    st.subheader("Temas")
    tipo = st.selectbox("Filtrar por tipo", ["Todos","caso","transversal","livre"])
    st.dataframe(themes_view(tipo), use_container_width=True)

    st.markdown("**Adicionar temas (JSON/CSV)**")
    up = st.file_uploader("Arquivo", type=["json","csv"], key="add_themes")
    if up:
        if up.name.endswith(".json"):
            raw = json.load(up)
            new = []
            for x in raw:
                if isinstance(x,str):
                    new.append({"title":x,"type":"caso"})
                elif isinstance(x,dict) and "title" in x:
                    new.append({"title":x["title"],"type":x.get("type","caso")})
        else:
            df = pd.read_csv(up)
            if "title" not in df.columns:
                st.error("CSV precisa da coluna 'title'. (opcional: 'type')")
                new = []
            else:
                new = [{"title":r["title"],"type":(r["type"] if "type" in df.columns else "caso")} for _,r in df.iterrows()]
        added = 0
        with engine.begin() as conn:
            mx = pd.read_sql("SELECT MAX(COALESCE(number,0)) mx FROM themes", conn)["mx"].iloc[0] or 0
            have = set(pd.read_sql("SELECT title FROM themes", conn)["title"].tolist())
            for it in new:
                if it["title"] in have: continue
                mx += 1
                conn.execute(text("""INSERT INTO themes(title,number,type,status) VALUES(:t,:n,:tp,'livre')"""),
                             {"t":it["title"],"n":int(mx),"tp":it["type"]})
                added += 1
        st.success(f"{added} tema(s) adicionados.")

    st.markdown("**Editar tema existente**")
    all_titles = get_df("SELECT title FROM themes ORDER BY number")["title"].tolist()
    if all_titles:
        to_edit = st.selectbox("Tema", all_titles, key="edit_theme")
        current = get_df("SELECT title,type FROM themes WHERE title=:t", t=to_edit).iloc[0]
        new_title = st.text_input("Novo título", value=current["title"])
        new_type = st.selectbox("Novo tipo", ["caso","transversal","livre"], index=["caso","transversal","livre"].index(current["type"] if current["type"] in ["caso","transversal","livre"] else "caso"))
        if st.button("Salvar edição"):
            try:
                exec_sql("UPDATE themes SET title=:nt, type=:tp WHERE title=:ot", nt=new_title.strip(), tp=new_type, ot=to_edit)
                st.success("Atualizado.")
            except Exception as e:
                st.error(f"Erro: {e}")

    st.markdown("—")
    st.subheader("Reservar tema")
    if not gdf.empty:
        sg = st.selectbox("Grupo", gdf["code"].tolist(), key="reserve_g")
        t_free = get_df("SELECT title FROM themes WHERE status='livre' ORDER BY number")["title"].tolist()
        tt = st.selectbox("Tema disponível", t_free)
        if st.button("Reservar"):
            ok, msg = reserve_theme(tt, sg)
            st.success(msg) if ok else st.error(msg)

        mine = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=sg)["title"].tolist()
        if mine:
            t_rel = st.selectbox("Liberar tema do meu grupo", mine)
            if st.button("Liberar"):
                ok, msg = release_theme(t_rel)
                st.warning(msg) if ok else st.error(msg)

    st.markdown("—")
    st.subheader("Grupos com contagem de membros")
    st.dataframe(groups_with_counts_df(), use_container_width=True)

with tabs[1]:
    st.subheader("Upload de Trabalhos")
    gdf = list_groups()
    if gdf.empty:
        st.info("Crie um grupo primeiro.")
    else:
        group = st.selectbox("Grupo", gdf["code"].tolist())
        tdf = get_df("SELECT title FROM themes WHERE reserved_by=:g", g=group)
        theme = tdf["title"].iloc[0] if not tdf.empty else None
        if not theme:
            st.error("Este grupo ainda não reservou um tema.")
        else:
            st.write("Tema do grupo: **", theme, "**")
            report = st.file_uploader("Relatório (PDF)", type=["pdf"])
            slides = st.file_uploader("Apresentação (PPTX ou PDF)", type=["pptx","pdf"])
            bundle = st.file_uploader("Materiais adicionais (ZIP)", type=["zip"])
            video = st.text_input("Link do vídeo")
            consent = st.checkbox("Cedo direitos patrimoniais à PUC‑SP para divulgação extensionista, com crédito aos autores.")
            who = st.text_input("Seu nome")
            if st.button("Enviar"):
                if not consent:
                    st.error("É necessário marcar a cessão de direitos.")
                else:
                    gdir = os.path.join(UPLOAD_DIR, group.replace('/','_'))
                    os.makedirs(gdir, exist_ok=True)
                    def save(up, name):
                        if not up: return None
                        p = os.path.join(gdir, name)
                        with open(p,"wb") as f: f.write(up.getbuffer())
                        return p
                    rpath = save(report,"relatorio.pdf")
                    spath = save(slides,"apresentacao."+ (slides.name.split('.')[-1] if slides else "pdf"))
                    zpath = save(bundle,"materiais.zip")
                    exec_sql("""INSERT INTO submissions(group_code,theme_title,report_path,slides_path,zip_path,video_link,
                              consent,submitted_by,submitted_at,approved)
                              VALUES(:g,:t,:r,:s,:z,:v,:c,:u,:ts,0)""",
                             g=group, t=theme, r=rpath, s=spath, z=zpath, v=video, c=1, u=who.strip(),
                             ts=datetime.now().isoformat(timespec="seconds"))
                    st.success("Submissão recebida.")

with tabs[2]:
    st.subheader("Exportar aprovados")
    admin_pwd = st.text_input("Senha (professor)", type="password")
    if admin_pwd == st.secrets.get("ADMIN_PWD","admin"):
        approved = get_df("SELECT group_code, theme_title, submitted_at, video_link FROM submissions WHERE approved=1 ORDER BY submitted_at DESC")
        if st.button("Gerar vitrine"):
            out = []
            for _,row in approved.iterrows():
                members = group_details(row["group_code"])
                out.append({"group":row["group_code"],"theme":row["theme_title"],
                            "members":members,"submitted_at":row["submitted_at"],"video_link":row["video_link"]})
            with open(os.path.join("public","submissions.json"),"w",encoding="utf-8") as f:
                json.dump(out,f,ensure_ascii=False,indent=2)
            html = "<html><body><h1>Galeria</h1>" + "<br/>".join([f"{i+1}. {d['group']} — {d['theme']}" for i,d in enumerate(out)]) + "</body></html>"
            with open(os.path.join("public","index.html"),"w",encoding="utf-8") as f: f.write(html)
            st.success("Gerado em ./public")
            st.download_button("Baixar submissions.json", data=open(os.path.join("public","submissions.json"),"rb").read(), file_name="submissions.json")
            st.download_button("Baixar index.html", data=open(os.path.join("public","index.html"),"rb").read(), file_name="index.html")

    st.markdown("---")
    st.subheader("Avaliação por docentes")
    email = st.text_input("E-mail institucional")
    pin = st.text_input("PIN", type="password")
    ok = False
    if st.button("Entrar"):
        dfp = get_df("SELECT * FROM professors WHERE email=:e AND pin=:p", e=email, p=pin)
        ok = not dfp.empty
        st.success("Acesso ok.") if ok else st.error("Credenciais inválidas.")
    if ok:
        sdf = get_df("""SELECT s.id, s.group_code, s.theme_title, s.submitted_at,
                        COALESCE(r.score,'') AS sua_nota, COALESCE(r.liked,0) AS seu_like
                        FROM submissions s LEFT JOIN ratings r
                        ON r.submission_id=s.id AND r.professor_email=:e
                        WHERE s.approved=1 ORDER BY s.submitted_at DESC""", e=email)
        st.dataframe(sdf, use_container_width=True)
        sid = st.selectbox("Trabalho (ID)", sdf["id"].tolist() if not sdf.empty else [])
        like = st.toggle("Curtir")
        score = st.slider("Nota", 0, 10, 8)
        comment = st.text_area("Comentário")
        if st.button("Salvar avaliação"):
            exec_sql("""INSERT INTO ratings(submission_id,professor_email,liked,score,comment,created_at)
                        VALUES(:i,:e,:l,:s,:c,:ts)
                        ON CONFLICT(submission_id, professor_email) DO UPDATE
                        SET liked=:l, score=:s, comment=:c, created_at=:ts""",
                     i=int(sid), e=email, l=int(like), s=int(score), c=comment,
                     ts=datetime.now().isoformat(timespec="seconds"))
            st.success("Ok.")

with tabs[3]:
    st.subheader("Aprovar submissões")
    sdf = get_df("SELECT id, group_code, theme_title, submitted_at, approved FROM submissions ORDER BY submitted_at DESC")
    st.dataframe(sdf, use_container_width=True)
    ids = st.multiselect("IDs", sdf["id"].tolist())
    if st.button("Aprovar"):
        for i in ids:
            exec_sql("UPDATE submissions SET approved=1 WHERE id=:i", i=int(i))
        st.success("Aprovadas.")

    st.markdown("---")
    st.subheader("Métricas")
    m = get_df("""SELECT s.id, s.group_code, s.theme_title,
                  AVG(r.score) AS media, SUM(r.liked) AS likes
                  FROM submissions s LEFT JOIN ratings r ON r.submission_id=s.id
                  GROUP BY s.id ORDER BY likes DESC, media DESC""")
    st.dataframe(m, use_container_width=True)

with tabs[4]:
    st.subheader("Importar alunos (CSV) — colunas: ra,name,email,turma")
    up_alunos = st.file_uploader("Arquivo CSV", type=["csv"])
    if up_alunos:
        df = pd.read_csv(up_alunos)
        with engine.begin() as conn:
            for row in df.to_dict(orient="records"):
                conn.execute(text("""INSERT OR IGNORE INTO students(ra,name,email,turma)
                                  VALUES(:ra,:name,:email,:turma)"""), row)
        st.success(f"{len(df)} aluno(s) processados).")

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

    st.markdown("---")
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

st.caption("MVP – temas com tipo (caso/transversal/livre), filtros, edição e controle de grupos.")
