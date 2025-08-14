def login_block():
    st.subheader("Login")
    who = st.radio("Sou:", ["Aluno","Docente"], horizontal=True)

    if who == "Docente":
        email = st.text_input("E-mail institucional", key="doc_email")
        pin   = st.text_input("PIN", type="password", key="doc_pin")

        if st.button("Entrar (docente)", key="doc_login_btn"):
            email_norm = (email or "").strip().lower()

            with engine.begin() as conn:
                # 1) Busca como dict (mappings)
                prof = conn.execute(
                    text("""
                        SELECT id, name, email, role, pin, approved
                          FROM professors
                         WHERE LOWER(email)=:e
                    """),
                    {"e": email_norm}
                ).mappings().fetchone()   # <- ESSENCIAL

                # 2) Auto-provisiona admin pelo ADMIN_EMAIL (se quiser manter)
                if not prof and ADMIN_EMAIL and email_norm == ADMIN_EMAIL.lower():
                    conn.execute(text("""
                        INSERT INTO professors(name,email,role,pin,approved)
                        VALUES('Administrador', :e, 'admin', :p, 1)
                    """), {"e": email_norm, "p": (pin or ADMIN_PIN)})

                    # Reconsulta já como dict
                    prof = conn.execute(
                        text("""
                            SELECT id, name, email, role, pin, approved
                              FROM professors
                             WHERE LOWER(email)=:e
                        """),
                        {"e": email_norm}
                    ).mappings().fetchone()

                # 3) Validações
                if not prof:
                    st.error("Conta de docente não encontrada. Solicite acesso na aba Administração.")
                elif int(prof.get("approved", 0)) != 1:
                    st.warning("Conta pendente de aprovação.")
                elif (pin or "") != (prof.get("pin") or ""):
                    st.error("PIN inválido.")
                else:
                    # sucesso
                    st.session_state["auth"] = {
                        "who": "docente",
                        "id": int(prof["id"]),
                        "email": prof["email"],
                        "name": prof["name"],
                        "role": prof.get("role") or "docente",
                    }
                    st.success("Login efetuado.")
                    st.experimental_rerun()

    else:
        ra = st.text_input("RA", key="al_ra")
        if st.button("Entrar (aluno)", key="al_login_btn"):
            df = get_df("SELECT id,ra,name,email,turma FROM students WHERE ra=:r AND active=1", r=(ra or "").strip())
            if df.empty:
                st.error("RA não encontrado. Solicite cadastro na aba Alunos & Docentes.")
            else:
                row = df.iloc[0]
                st.session_state["auth"] = {
                    "who":"aluno",
                    "id":int(row["id"]),
                    "ra":row["ra"],
                    "name":row["name"],
                    "turma":row["turma"],
                    "email":row["email"],
                }
                st.success(f"Bem-vindo(a), {row['name']}!")
                st.experimental_rerun()
