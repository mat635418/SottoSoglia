"""SottoSoglia — simulatore della trattenuta per uso privato dell'auto aziendale.

Il dipendente dichiara i propri fringe benefit, l'app calcola il valore annuo
complessivo e propone la trattenuta per uso privato auto che lo mantiene sotto il
limite di esenzione fiscale (1.000 / 2.000 Euro), a condizione che la trattenuta
costi meno della tassazione che si subirebbe superando il limite.

Regole di calcolo: "Guida alla tassazione dei fringe benefit" (agg. 13/02/2026).
Perimetro e output: "Progetto uso privato self service".
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# --------------------------------------------------------------------------- #
# Parametri normativi                                                         #
# --------------------------------------------------------------------------- #

LIMITE_SENZA_FIGLI = 1_000.00
LIMITE_CON_FIGLI = 2_000.00

# Il totale dei fringe benefit non deve avvicinarsi troppo al limite: teniamo un
# margine di sicurezza (richiesta del documento di progetto).
MARGINE_SICUREZZA = 50.00

# Trattenuta standard applicata quando personalizzarla non conviene.
TRATTENUTA_BASE_ANNUA = 140.00

# Versione beta: superato il limite si assume che metà dell'imponibile si traduca
# in tasse e contributi (slide "Auto ad uso promiscuo - busta paga").
ALIQUOTA_TASSAZIONE = 0.50

# Assicurazioni, valori 2026 (slide "Valorizzazione assicurazioni").
IPM_NON_DIRIGENTE = 103.39
IPM_DIRIGENTE = 145.93
INFORTUNI_TASSO = 0.00246456
INFORTUNI_CONTRIBUTO_DIPENDENTE = 7.28

# Listino aziendale: marca, modello, allestimento e fringe benefit annuo lordo.
PERCORSO_LISTINO = Path(__file__).parent / "lista_Auto.xlsx"
COL_MARCA = "Marca"
COL_MODELLO = "Modello"
COL_ALLESTIMENTO = "Allestimento"
COL_FRINGE = "Fringe Benefit Annuale"
COLONNE_LISTINO = [COL_MARCA, COL_MODELLO, COL_ALLESTIMENTO, COL_FRINGE]

ANNO_DEFAULT = 2026

# Colori di serie del grafico, dal tema della presentazione.
SERIE_TRATTENUTA = "Trattenuta per uso privato"
SERIE_TASSAZIONE = "Tasse e contributi stimati"
COLORI_SERIE_LIGHT = {SERIE_TRATTENUTA: "#20313D", SERIE_TASSAZIONE: "#D30B1C"}
COLORI_SERIE_DARK = {SERIE_TRATTENUTA: "#BBD4D3", SERIE_TASSAZIONE: "#E75D46"}


# --------------------------------------------------------------------------- #
# Motore di calcolo (funzioni pure, nessuna dipendenza da Streamlit)          #
# --------------------------------------------------------------------------- #


def giorni_nell_anno(anno: int) -> int:
    return 366 if calendar.isleap(anno) else 365


def fringe_auto_maturato(valore_annuo_lordo: float, dal: date, anno: int) -> tuple[float, int]:
    """Fringe auto lordo che matura da `dal` al 31/12, pro-rata sui giorni di possesso."""
    giorni_totali = giorni_nell_anno(anno)
    giorni = (date(anno, 12, 31) - dal).days + 1
    giorni = max(0, min(giorni, giorni_totali))
    return valore_annuo_lordo * giorni / giorni_totali, giorni


def valore_ipm(aderente: bool, dirigente: bool) -> float:
    if not aderente:
        return 0.0
    return IPM_DIRIGENTE if dirigente else IPM_NON_DIRIGENTE


def valore_infortuni(aderente: bool, ral: float, dirigente: bool) -> float:
    """RAL x 0,246456%, al netto del contributo trattenuto al dipendente."""
    if not aderente:
        return 0.0
    lordo = ral * INFORTUNI_TASSO
    if dirigente:
        return max(0.0, lordo)
    return max(0.0, lordo - INFORTUNI_CONTRIBUTO_DIPENDENTE)


def normalizza_listino(dati: pd.DataFrame) -> pd.DataFrame:
    """Pulisce il listino auto: colonne attese, spazi ripetuti, valori numerici."""
    dati = dati.rename(columns=lambda c: str(c).strip())
    mancanti = [c for c in COLONNE_LISTINO if c not in dati.columns]
    if mancanti:
        raise ValueError("colonne mancanti nel listino: " + ", ".join(mancanti))
    dati = dati[COLONNE_LISTINO].copy()
    for colonna in (COL_MARCA, COL_MODELLO, COL_ALLESTIMENTO):
        dati[colonna] = (
            dati[colonna].astype("string").str.replace(r"\s+", " ", regex=True).str.strip()
        )
    dati[COL_FRINGE] = pd.to_numeric(dati[COL_FRINGE], errors="coerce")
    dati = dati.dropna().drop_duplicates()
    return dati.sort_values(COLONNE_LISTINO).reset_index(drop=True)


@dataclass(frozen=True)
class Scenario:
    """Esito annuo per un dato livello di trattenuta."""

    trattenuta_annua: float
    trattenuta_mensile: float
    trattenuta_pagata: float
    totale_fringe: float
    imponibile: float
    tassazione: float
    costo_totale: float
    margine: float
    sopra_limite: bool


def valuta_scenario(
    trattenuta_annua: float,
    *,
    fringe_lordo: float,
    mesi: int,
    limite: float,
) -> Scenario:
    """Valuta un livello di trattenuta.

    `fringe_lordo` è il totale annuo dei fringe benefit prima della nuova
    trattenuta: fringe auto già in busta paga + fringe auto lordo del periodo
    residuo + assicurazioni.

    La soglia di esenzione è uno "scalino": se il totale la supera, l'imponibile
    è l'intero valore dei fringe benefit, non la sola eccedenza.
    """
    pagata = trattenuta_annua * mesi / 12
    totale = max(0.0, fringe_lordo - pagata)
    sopra_limite = totale > limite
    imponibile = totale if sopra_limite else 0.0
    tassazione = imponibile * ALIQUOTA_TASSAZIONE
    return Scenario(
        trattenuta_annua=trattenuta_annua,
        trattenuta_mensile=trattenuta_annua / 12,
        trattenuta_pagata=pagata,
        totale_fringe=totale,
        imponibile=imponibile,
        tassazione=tassazione,
        costo_totale=pagata + tassazione,
        margine=limite - totale,
        sopra_limite=sopra_limite,
    )


def trattenuta_necessaria(*, fringe_lordo: float, mesi: int, obiettivo: float) -> float:
    """Trattenuta annua minima per portare il totale a `obiettivo`."""
    if mesi <= 0:
        return math.inf
    return (fringe_lordo - obiettivo) * 12 / mesi


def tetto_trattenuta(*, fringe_auto_residuo: float, mesi: int) -> float:
    """Trattenuta annua oltre la quale il fringe auto del periodo residuo si azzera."""
    if mesi <= 0:
        return 0.0
    return fringe_auto_residuo * 12 / mesi


# --------------------------------------------------------------------------- #
# Formattazione                                                               #
# --------------------------------------------------------------------------- #


def euro(valore: float, decimali: int = 2) -> str:
    testo = f"{valore:,.{decimali}f}"
    intero, _, dec = testo.partition(".")
    intero = intero.replace(",", ".")
    return f"{intero},{dec} €" if dec else f"{intero} €"


def percento(valore: float) -> str:
    return f"{valore * 100:.2f}".rstrip("0").rstrip(".").replace(".", ",") + "%"


# --------------------------------------------------------------------------- #
# Pagina                                                                      #
# --------------------------------------------------------------------------- #

st.set_page_config(
    page_title="SottoSoglia",
    page_icon=":material/shield_with_heart:",
    layout="wide",
)

_logo = Path(__file__).parent / "logo.png"
if _logo.exists():
    st.logo(str(_logo), size="large")


@st.cache_data(show_spinner=False)
def carica_listino(percorso: Path, firma: float) -> pd.DataFrame:
    """Legge il listino auto dall'Excel. `firma` invalida la cache se il file cambia."""
    del firma  # serve solo come chiave di cache
    return normalizza_listino(pd.read_excel(percorso))


def listino_auto() -> pd.DataFrame | None:
    """Listino pronto all'uso, o None se il file manca o non è leggibile."""
    if not PERCORSO_LISTINO.exists():
        st.warning(
            f"Non trovo il listino **{PERCORSO_LISTINO.name}** accanto a `app.py`: "
            "senza di esso puoi solo inserire a mano il fringe benefit annuo.",
            icon=":material/folder_off:",
        )
        return None
    try:
        return carica_listino(PERCORSO_LISTINO, PERCORSO_LISTINO.stat().st_mtime)
    except Exception as errore:  # file corrotto, colonne rinominate, foglio vuoto
        st.error(
            f"Il listino **{PERCORSO_LISTINO.name}** non è leggibile ({errore}). "
            f"Attese le colonne: {', '.join(COLONNE_LISTINO)}.",
            icon=":material/error:",
        )
        return None

st.title("SottoSoglia")
st.caption(
    "Il tuo fringe benefit, sempre sotto il limite. "
    "Simula il valore annuo dei tuoi fringe benefit e scopri quale trattenuta per "
    "uso privato dell'auto ti conviene scegliere."
)

# --- Sidebar: profilo fiscale e assicurazioni ------------------------------ #

with st.sidebar:
    st.subheader("Il tuo profilo")
    anno = st.number_input(
        "Anno di riferimento",
        min_value=2025,
        max_value=2030,
        value=ANNO_DEFAULT,
        step=1,
        format="%d",
    )
    anno = int(anno)

    figli_a_carico = st.toggle(
        "Ho figli a carico",
        value=False,
        help=(
            "Figli fino a 24 anni con reddito annuo non superiore a 4.000 €, oppure "
            "oltre 24 anni con reddito non superiore a 2.840,51 €. Il limite di "
            "esenzione passa da 1.000 a 2.000 €, previa dichiarazione all'azienda."
        ),
    )
    dirigente = st.toggle(
        "Sono dirigente",
        value=False,
        help="Cambia la quota azienda dell'assicurazione invalidità permanente.",
    )

    limite = LIMITE_CON_FIGLI if figli_a_carico else LIMITE_SENZA_FIGLI
    obiettivo = limite - MARGINE_SICUREZZA

    st.metric("Limite di esenzione", euro(limite, 0), border=True)
    if figli_a_carico:
        st.caption(
            "Ricorda di inviare la dichiarazione con il codice fiscale dei figli a "
            "HRInfinity@cchellenic.com: senza dichiarazione il limite resta 1.000 €."
        )

    st.subheader("Assicurazioni")
    aderente_ipm = st.toggle("Invalidità permanente da malattia (IPM)", value=False)
    aderente_infortuni = st.toggle("Infortuni extraprofessionali", value=False)
    ral = 0.0
    if aderente_infortuni:
        ral = st.number_input(
            "RAL soggetta a INPS (€)",
            min_value=0.0,
            max_value=500_000.0,
            value=35_000.0,
            step=1_000.0,
        )

fringe_ipm = valore_ipm(aderente_ipm, dirigente)
fringe_infortuni = valore_infortuni(aderente_infortuni, ral, dirigente)
fringe_assicurazioni = fringe_ipm + fringe_infortuni

# --- Input auto ------------------------------------------------------------ #

st.header("La tua auto aziendale")

with st.container(border=True):
    col_a, col_b, col_c = st.columns(3)

    with col_a:
        # Il valore si imposta anche dal listino: la chiave va inizializzata qui,
        # senza `value=`, altrimenti Streamlit segnala il doppio default.
        st.session_state.setdefault("fringe_auto_lordo", 4_471.56)
        st.number_input(
            "Fringe benefit auto annuo, al lordo della trattenuta (€)",
            min_value=0.0,
            max_value=50_000.0,
            step=50.0,
            key="fringe_auto_lordo",
            help=(
                "Valore annuo da tabelle ACI, prima di sottrarre la trattenuta per "
                "uso privato. Se non lo conosci, scegli l'auto dal listino qui sotto."
            ),
        )
        data_assegnazione = st.date_input(
            "Data di assegnazione dell'auto",
            value=date(anno, 1, 1),
            min_value=date(anno - 10, 1, 1),
            max_value=date(anno, 12, 31),
            format="DD/MM/YYYY",
            help=(
                "Se possiedi l'auto da prima del 1° gennaio, lascia il 1° gennaio: "
                "il benefit matura sui giorni di possesso nell'anno."
            ),
        )

    with col_b:
        data_scelta = st.date_input(
            "Data da cui decorre la nuova trattenuta",
            value=date(anno, 1, 1),
            min_value=date(anno, 1, 1),
            max_value=date(anno, 12, 31),
            format="DD/MM/YYYY",
            help="Primo mese di busta paga in cui verrà applicata la trattenuta scelta.",
        )
        fringe_ytd = st.number_input(
            "Fringe auto già in busta paga fino a quella data (€)",
            min_value=0.0,
            max_value=50_000.0,
            value=0.0,
            step=50.0,
            help=(
                "Somma della voce paga «Benefit Auto» dei mesi già elaborati. "
                "È già al netto della trattenuta per uso privato applicata finora."
            ),
        )

    with col_c:
        trattenuta_attuale = st.number_input(
            "Trattenuta annua attualmente applicata (€)",
            min_value=0.0,
            max_value=50_000.0,
            value=TRATTENUTA_BASE_ANNUA,
            step=10.0,
            help="Serve solo come confronto. La trattenuta base aziendale è 140 € annui.",
        )
        st.space("small")
        st.caption(
            f"Benefit assicurativi conteggiati: **{euro(fringe_assicurazioni)}** "
            f"(IPM {euro(fringe_ipm)} + infortuni {euro(fringe_infortuni)})."
        )

    with st.expander(
        "Non conosci il valore? Scegli l'auto dal listino",
        icon=":material/directions_car:",
        expanded=True,
    ):
        listino = listino_auto()

        if listino is not None:
            st.caption(
                f"{len(listino)} valorizzazioni in listino, da "
                f"{listino[COL_MARCA].nunique()} marche. Scegli marca, modello e "
                "allestimento: il fringe benefit annuo lordo arriva dal listino."
            )

            c1, c2, c3 = st.columns(3)
            with c1:
                marca = st.selectbox(
                    "Marca",
                    sorted(listino[COL_MARCA].unique()),
                    index=None,
                    placeholder="Scegli la marca",
                )
            modelli = (
                sorted(listino.loc[listino[COL_MARCA] == marca, COL_MODELLO].unique())
                if marca
                else []
            )
            with c2:
                modello = st.selectbox(
                    "Modello",
                    modelli,
                    index=None,
                    placeholder="Scegli il modello" if marca else "Scegli prima la marca",
                    disabled=not marca,
                )
            selezione = (
                listino[(listino[COL_MARCA] == marca) & (listino[COL_MODELLO] == modello)]
                if marca and modello
                else listino.iloc[0:0]
            )
            with c3:
                allestimento = st.selectbox(
                    "Allestimento",
                    sorted(selezione[COL_ALLESTIMENTO].unique()),
                    index=None,
                    placeholder="Scegli l'allestimento"
                    if modello
                    else "Scegli prima il modello",
                    disabled=selezione.empty,
                )

            if allestimento:
                valori = sorted(
                    selezione.loc[
                        selezione[COL_ALLESTIMENTO] == allestimento, COL_FRINGE
                    ].unique()
                )
                if len(valori) > 1:
                    # 21 allestimenti su 121 compaiono con due valorizzazioni (regimi
                    # fiscali diversi): sceglie l'utente, proposta la più prudente.
                    valore_listino = st.radio(
                        "Il listino riporta più valorizzazioni per questo allestimento",
                        valori,
                        index=len(valori) - 1,
                        format_func=euro,
                        horizontal=True,
                        help=(
                            "Dipende dal regime fiscale applicabile all'auto, che "
                            "cambia con l'anno di ordine e di assegnazione. Il valore "
                            "esatto è nella tua lettera di assegnazione: in caso di "
                            "dubbio tieni il più alto, così la trattenuta proposta "
                            "non risulta insufficiente."
                        ),
                    )
                else:
                    valore_listino = valori[0]

                with st.container(horizontal=True, vertical_alignment="bottom"):
                    st.metric(
                        "Fringe benefit annuo lordo da listino",
                        euro(valore_listino),
                        border=True,
                    )
                    st.button(
                        "Usa questo valore",
                        icon=":material/arrow_upward:",
                        type="primary",
                        on_click=lambda v=valore_listino: st.session_state.update(
                            fringe_auto_lordo=round(float(v), 2)
                        ),
                    )
                st.caption(
                    f"**{marca} {allestimento}** — valore annuo al lordo della "
                    "trattenuta per uso privato e prima del pro-rata sui giorni di "
                    "possesso."
                )
            else:
                st.caption(
                    "Se l'auto non è in listino, inserisci il valore a mano nel campo "
                    "qui sopra: lo trovi nella lettera di assegnazione."
                )

fringe_auto_lordo_annuo = float(st.session_state["fringe_auto_lordo"])

# --- Calcolo --------------------------------------------------------------- #

inizio_residuo = max(data_scelta, data_assegnazione, date(anno, 1, 1))
fringe_auto_residuo, giorni_residui = fringe_auto_maturato(
    fringe_auto_lordo_annuo, inizio_residuo, anno
)
mesi_residui = 13 - inizio_residuo.month

fringe_lordo_totale = fringe_ytd + fringe_auto_residuo + fringe_assicurazioni

# Se la scelta decorre in corso d'anno, i mesi già elaborati vanno dichiarati:
# senza di essi il totale annuo è sottostimato e la trattenuta proposta troppo bassa.
if inizio_residuo > date(anno, 1, 1) and fringe_ytd == 0:
    st.warning(
        f"La trattenuta decorre dal {inizio_residuo.strftime('%d/%m/%Y')}, ma non hai "
        "indicato il fringe auto già presente in busta paga nei mesi precedenti. "
        "Senza quel valore il totale annuo è sottostimato e la trattenuta proposta "
        "risulta troppo bassa: riporta la somma della voce «Benefit Auto» dei "
        "cedolini già elaborati.",
        icon=":material/receipt_long:",
    )

necessaria = trattenuta_necessaria(
    fringe_lordo=fringe_lordo_totale, mesi=mesi_residui, obiettivo=obiettivo
)
tetto = tetto_trattenuta(fringe_auto_residuo=fringe_auto_residuo, mesi=mesi_residui)

if necessaria <= TRATTENUTA_BASE_ANNUA:
    stato = "gia_sotto"
    trattenuta_obiettivo = TRATTENUTA_BASE_ANNUA
elif necessaria > tetto:
    stato = "non_raggiungibile"
    trattenuta_obiettivo = math.floor(tetto)
else:
    stato = "raggiungibile"
    trattenuta_obiettivo = math.ceil(necessaria)

sc_base = valuta_scenario(
    TRATTENUTA_BASE_ANNUA, fringe_lordo=fringe_lordo_totale, mesi=mesi_residui, limite=limite
)
sc_attuale = valuta_scenario(
    trattenuta_attuale, fringe_lordo=fringe_lordo_totale, mesi=mesi_residui, limite=limite
)
sc_obiettivo = valuta_scenario(
    trattenuta_obiettivo, fringe_lordo=fringe_lordo_totale, mesi=mesi_residui, limite=limite
)

risparmio = sc_base.costo_totale - sc_obiettivo.costo_totale
conviene = risparmio > 0 and stato != "gia_sotto"

# La trattenuta consigliata vale solo se costa meno della tassazione evitata;
# altrimenti si propone la trattenuta base di 140 € annui.
sc_consigliato = sc_obiettivo if conviene else sc_base

# --- Risultato ------------------------------------------------------------- #

st.header("Il risultato")

with st.container(horizontal=True):
    st.metric(
        "Fringe benefit annui stimati",
        euro(sc_consigliato.totale_fringe),
        border=True,
        help="Totale annuo dei fringe benefit con la trattenuta consigliata.",
    )
    st.metric(
        "Trattenuta consigliata",
        euro(sc_consigliato.trattenuta_annua),
        delta=f"{euro(sc_consigliato.trattenuta_mensile)} al mese",
        delta_arrow="off",
        border=True,
    )
    st.metric(
        "Tasse e contributi stimati",
        euro(sc_consigliato.tassazione),
        border=True,
        help="Ipotesi beta: 50% dell'imponibile, se il limite viene superato.",
    )
    st.metric(
        "Risparmio rispetto alla trattenuta base",
        euro(max(0.0, risparmio)),
        border=True,
        help=f"Confronto con la trattenuta base di {euro(TRATTENUTA_BASE_ANNUA, 0)} annui.",
    )

if stato == "gia_sotto":
    st.success(
        f"Resti già sotto il limite: con la trattenuta base di "
        f"{euro(TRATTENUTA_BASE_ANNUA, 0)} annui i tuoi fringe benefit valgono "
        f"{euro(sc_base.totale_fringe)}, cioè {euro(sc_base.margine)} sotto il "
        f"limite di {euro(limite, 0)}. Non serve aumentare la trattenuta.",
        icon=":material/check_circle:",
    )
elif not conviene:
    st.warning(
        f"Conviene **non** personalizzare la trattenuta. Per scendere sotto il limite "
        f"servirebbero {euro(sc_obiettivo.trattenuta_annua)} annui "
        f"({euro(sc_obiettivo.trattenuta_pagata)} da qui a dicembre), più di quanto "
        f"pagheresti di tasse e contributi ({euro(sc_base.tassazione)}). "
        f"Mantieni la trattenuta base di {euro(TRATTENUTA_BASE_ANNUA, 0)} annui.",
        icon=":material/trending_down:",
    )
elif stato == "non_raggiungibile":
    st.warning(
        f"Il limite non è raggiungibile con la sola trattenuta: anche azzerando il "
        f"fringe auto del periodo residuo "
        f"({euro(sc_obiettivo.trattenuta_annua)} annui) il totale resterebbe a "
        f"{euro(sc_obiettivo.totale_fringe)}, sopra il limite di {euro(limite, 0)}. "
        f"La trattenuta massima utile riduce comunque il costo di {euro(risparmio)}.",
        icon=":material/warning:",
    )
else:
    st.success(
        f"Scegli una trattenuta di **{euro(sc_obiettivo.trattenuta_annua)} annui** "
        f"({euro(sc_obiettivo.trattenuta_mensile)} al mese): i tuoi fringe benefit "
        f"si fermano a {euro(sc_obiettivo.totale_fringe)}, "
        f"{euro(sc_obiettivo.margine)} sotto il limite di {euro(limite, 0)}, "
        f"e risparmi {euro(risparmio)} rispetto alla trattenuta base.",
        icon=":material/savings:",
    )

col_grafico, col_scenari = st.columns([3, 2], gap="medium")

with col_grafico:
    with st.container(border=True):
        st.subheader("Quanto ti costa ogni livello di trattenuta")
        st.caption(
            "Il costo totale annuo è la somma di quello che versi di trattenuta e di "
            "quello che paghi di tasse e contributi. Il punto più basso è la scelta "
            "più conveniente."
        )

        scuro = st.context.theme.type == "dark"
        colori = COLORI_SERIE_DARK if scuro else COLORI_SERIE_LIGHT
        colore_fondo = "#152028" if scuro else "#FFFFFF"
        colore_annotazione = "#EAEDEF" if scuro else "#20313D"

        massimo_x = max(tetto, trattenuta_obiettivo, trattenuta_attuale, 600.0) * 1.15
        passi = [massimo_x * i / 120 for i in range(121)]
        righe = []
        for t in passi:
            sc = valuta_scenario(
                t, fringe_lordo=fringe_lordo_totale, mesi=mesi_residui, limite=limite
            )
            righe.append(
                {
                    "trattenuta": t,
                    "voce": SERIE_TRATTENUTA,
                    "valore": sc.trattenuta_pagata,
                    "costo_totale": sc.costo_totale,
                }
            )
            righe.append(
                {
                    "trattenuta": t,
                    "voce": SERIE_TASSAZIONE,
                    "valore": sc.tassazione,
                    "costo_totale": sc.costo_totale,
                }
            )
        curva = pd.DataFrame(righe)

        aree = (
            alt.Chart(curva)
            .mark_area(stroke=colore_fondo, strokeWidth=1, interpolate="linear")
            .encode(
                x=alt.X(
                    "trattenuta:Q",
                    title="Trattenuta annua per uso privato (€)",
                    axis=alt.Axis(format=",.0f", grid=False),
                ),
                y=alt.Y(
                    "valore:Q",
                    title="Costo annuo (€)",
                    stack=True,
                    axis=alt.Axis(format=",.0f", gridOpacity=0.25),
                ),
                color=alt.Color(
                    "voce:N",
                    title=None,
                    scale=alt.Scale(domain=list(colori), range=list(colori.values())),
                    legend=alt.Legend(orient="top", direction="horizontal"),
                ),
                order=alt.Order("voce:N", sort="ascending"),
                tooltip=[
                    alt.Tooltip("trattenuta:Q", title="Trattenuta annua", format=",.0f"),
                    alt.Tooltip("voce:N", title="Voce"),
                    alt.Tooltip("valore:Q", title="Importo", format=",.2f"),
                    alt.Tooltip("costo_totale:Q", title="Costo totale", format=",.2f"),
                ],
            )
        )

        consigliato = pd.DataFrame(
            [
                {
                    "trattenuta": sc_consigliato.trattenuta_annua,
                    "costo_totale": sc_consigliato.costo_totale,
                    "etichetta": f"Consigliata · {euro(sc_consigliato.trattenuta_annua)}",
                }
            ]
        )
        linea = (
            alt.Chart(consigliato)
            .mark_rule(color=colore_annotazione, strokeDash=[4, 4], strokeWidth=2)
            .encode(x="trattenuta:Q")
        )
        punto = (
            alt.Chart(consigliato)
            .mark_point(
                size=130,
                filled=True,
                color=colore_annotazione,
                stroke=colore_fondo,
                strokeWidth=2,
            )
            .encode(x="trattenuta:Q", y="costo_totale:Q")
        )
        etichetta = (
            alt.Chart(consigliato)
            .mark_text(
                align="left", dx=10, dy=-12, fontWeight="bold", color=colore_annotazione
            )
            .encode(x="trattenuta:Q", y="costo_totale:Q", text="etichetta:N")
        )

        st.altair_chart(
            (aree + linea + punto + etichetta).properties(height=330),
            width="stretch",
        )

with col_scenari:
    with st.container(border=True):
        st.subheader("Confronto degli scenari")
        scenari = {
            f"Base ({euro(TRATTENUTA_BASE_ANNUA, 0)})": sc_base,
            f"Attuale ({euro(trattenuta_attuale, 0)})": sc_attuale,
            "Consigliata": sc_consigliato,
        }
        tabella = pd.DataFrame(
            [
                {
                    "Scenario": nome,
                    "Trattenuta annua": sc.trattenuta_annua,
                    "Versata nell'anno": sc.trattenuta_pagata,
                    "Fringe benefit": sc.totale_fringe,
                    "Imponibile": sc.imponibile,
                    "Tasse e contributi": sc.tassazione,
                    "Costo totale": sc.costo_totale,
                    "Margine dal limite": sc.margine,
                }
                for nome, sc in scenari.items()
            ]
        )
        st.dataframe(
            tabella,
            hide_index=True,
            width="stretch",
            column_config={
                col: st.column_config.NumberColumn(col, format="euro")
                for col in tabella.columns
                if col != "Scenario"
            },
        )
        st.caption(
            "«Versata nell'anno» tiene conto dei soli mesi residui: la trattenuta "
            f"annua viene applicata per {mesi_residui} mesi su 12."
        )

# --- Dettaglio ------------------------------------------------------------- #

with st.expander("Dettaglio del calcolo", icon=":material/function:"):
    dettaglio = pd.DataFrame(
        [
            {
                "Voce": "Fringe auto già in busta paga",
                "Importo": fringe_ytd,
                "Nota": f"Voce «Benefit Auto» fino al {inizio_residuo.strftime('%d/%m/%Y')}, netto uso privato",
            },
            {
                "Voce": "Fringe auto del periodo residuo (lordo)",
                "Importo": fringe_auto_residuo,
                "Nota": f"{euro(fringe_auto_lordo_annuo)} annui × {giorni_residui} giorni / {giorni_nell_anno(anno)}",
            },
            {
                "Voce": "Assicurazione invalidità permanente",
                "Importo": fringe_ipm,
                "Nota": "Quota azienda, gestita nel cedolino di dicembre"
                if aderente_ipm
                else "Non aderente",
            },
            {
                "Voce": "Assicurazione infortuni extraprofessionali",
                "Importo": fringe_infortuni,
                "Nota": f"{euro(ral)} × 0,246456% − {euro(INFORTUNI_CONTRIBUTO_DIPENDENTE)}"
                if aderente_infortuni and not dirigente
                else ("RAL × 0,246456%" if aderente_infortuni else "Non aderente"),
            },
            {
                "Voce": "Totale prima della nuova trattenuta",
                "Importo": fringe_lordo_totale,
                "Nota": "Base di partenza del calcolo",
            },
            {
                "Voce": "Trattenuta consigliata versata nell'anno",
                "Importo": -sc_consigliato.trattenuta_pagata,
                "Nota": f"{euro(sc_consigliato.trattenuta_mensile)} × {mesi_residui} mesi",
            },
            {
                "Voce": "Fringe benefit annui finali",
                "Importo": sc_consigliato.totale_fringe,
                "Nota": f"Limite {euro(limite, 0)}, obiettivo {euro(obiettivo, 0)} (margine di sicurezza {euro(MARGINE_SICUREZZA, 0)})",
            },
        ]
    )
    st.dataframe(
        dettaglio,
        hide_index=True,
        width="stretch",
        column_config={
            "Importo": st.column_config.NumberColumn("Importo", format="euro"),
            "Nota": st.column_config.TextColumn("Nota", width="large"),
        },
    )

with st.expander("Le regole applicate", icon=":material/gavel:"):
    st.markdown(
        f"""
**Limite di esenzione** — fino al periodo d'imposta 2027 i fringe benefit non
concorrono al reddito entro {euro(LIMITE_SENZA_FIGLI, 0)} annui, elevati a
{euro(LIMITE_CON_FIGLI, 0)} per i lavoratori con figli a carico che lo dichiarano
all'azienda.

**Superamento del limite** — la soglia è uno scalino: appena il totale annuo la
supera, l'imponibile è l'intero valore dei fringe benefit, non la sola eccedenza,
e vengono assoggettati anche i mesi pregressi. In questa versione beta si assume
che il {percento(ALIQUOTA_TASSAZIONE)} dell'imponibile si traduca in tasse e
contributi.

**Fringe benefit auto ad uso promiscuo** — costo chilometrico da tabelle ACI ×
15.000 km convenzionali × aliquota di legge (10% elettriche, 20%
plug-in hybrid, 50% le altre motorizzazioni), maggiorata del 5% in presenza di
accessori non valorizzati nelle tabelle ACI e del 50% dopo il quinto anno
dall'immatricolazione. Il calcolo è già fatto nel listino `lista_Auto.xlsx`, da
cui l'app prende il valore annuo lordo dell'allestimento scelto; dal risultato si
sottrae la trattenuta per uso privato.

**Periodo di possesso** — il benefit matura dalla data di assegnazione dell'auto
alla data di restituzione, quindi il valore annuo è riproporzionato sui giorni di
possesso.

**Assicurazioni (valori 2026)** — invalidità permanente da malattia:
{euro(IPM_NON_DIRIGENTE)} per i non dirigenti, {euro(IPM_DIRIGENTE)} per i
dirigenti. Infortuni extraprofessionali: retribuzione annua soggetta a INPS ×
0,246456%, meno {euro(INFORTUNI_CONTRIBUTO_DIPENDENTE)} di contributo trattenuto
al dipendente non dirigenziale.

**Margine di sicurezza** — la trattenuta proposta porta il totale almeno
{euro(MARGINE_SICUREZZA, 0)} sotto il limite, per assorbire aggiornamenti delle
tabelle ACI o cambi auto in corso d'anno.

**Convenienza** — la trattenuta personalizzata viene proposta solo se costa meno
delle tasse e dei contributi che si pagherebbero superando il limite. Altrimenti
l'app propone la trattenuta base di {euro(TRATTENUTA_BASE_ANNUA, 0)} annui.
"""
    )
    st.caption(
        "Fonte: «Guida alla tassazione dei fringe benefit», aggiornata al 13 febbraio 2026."
    )

st.caption(
    ":material/info: Versione beta a fini orientativi. I valori sono stime: la "
    "tassazione effettiva dipende dalla tua situazione fiscale complessiva "
    "(detrazioni, trattamento integrativo, altri elementi retributivi del mese) e "
    "le tabelle ACI vengono aggiornate periodicamente. Per il tuo caso specifico "
    "rivolgiti al team HR."
)
