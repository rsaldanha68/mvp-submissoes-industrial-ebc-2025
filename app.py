st.header("Admin (Students)")

st.info(
    "Faça upload de um CSV por turma. Formato mínimo: "
    "**name,ra[,email]**. Se o email vier vazio, uso **RA@pucsp.edu.br**.\n\n"
    "Selecione abaixo a **Disciplina** e a **Turma** que este arquivo representa."
)

disc_label_to_obj = {
    "Economia Industrial": disc_ind,
    "Economia Brasileira Contemporânea II": disc_ebc,
}
sel_disc_label = st.selectbox("Disciplina deste CSV", list(disc_label_to_obj.keys()))
sel_disc = disc_label_to_obj[sel_disc_label]
sel_turma = st.text_input("Turma (ex.: MA6, MB6, NA6, NB6)")

up = st.file_uploader("CSV (colunas: name,ra[,email])", type=["csv"])

if up and st.button("Processar CSV desta turma"):
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
