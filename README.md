# SottoSoglia

**Il tuo fringe benefit, sempre sotto il limite.**

Simulatore self service della trattenuta per uso privato dell'auto aziendale.
Il dipendente dichiara i propri fringe benefit, l'app calcola il valore annuo
complessivo e propone l'importo della trattenuta che lo mantiene sotto il limite
di esenzione fiscale — ma solo quando quella trattenuta costa meno delle tasse e
dei contributi che si pagherebbero superando il limite.

## Avvio rapido

Doppio clic su **`launch_app.bat`**. Al primo avvio installa i pacchetti
necessari, poi apre il browser su <http://localhost:8501>.

In alternativa, da terminale:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Come funziona

### Cosa inserisce il dipendente

| Campo | Dove | Note |
|---|---|---|
| Anno di riferimento | sidebar | Default 2026 |
| Figli a carico | sidebar | Porta il limite da 1.000 a 2.000 € |
| Dirigente | sidebar | Cambia la quota azienda dell'assicurazione IPM |
| Adesione IPM | sidebar | 103,39 € (145,93 € dirigenti) |
| Adesione infortuni + RAL | sidebar | RAL × 0,246456% − 7,28 € |
| Fringe auto annuo **al lordo** della trattenuta | pagina | Si sceglie l'auto dal listino, oppure si digita il valore |
| Data di assegnazione dell'auto | pagina | Il benefit matura sui giorni di possesso |
| Data da cui decorre la nuova trattenuta | pagina | Permette di cambiare scelta in corso d'anno |
| Fringe auto già in busta paga a quella data | pagina | Voce «Benefit Auto», già netto uso privato |
| Trattenuta annua attuale | pagina | Solo per confronto |

### Il listino auto

Il fringe benefit dell'auto non si calcola a mano: si sceglie l'auto da
**`lista_Auto.xlsx`**, nella cartella dell'app, con tre menu a tendina a cascata
— **marca → modello → allestimento** — e l'app prende il valore dalla colonna
*Fringe Benefit Annuale*. Il pulsante «Usa questo valore» lo copia nel campo del
fringe auto annuo.

Il file deve avere queste quattro colonne (il foglio è il primo del workbook):

| Marca | Modello | Allestimento | Fringe Benefit Annuale |
|---|---|---|---|
| TOYOTA | Corolla Cross | Corolla Cross 1.8 Hybrid 140 CV E-CVT Trend | 4471,56 |

Per aggiornare il listino basta sostituire il file: viene riletto quando cambia
la data di modifica (cache su `st.cache_data`). Spazi multipli e righe duplicate
vengono normalizzati in lettura. Se il file manca o ha colonne diverse, l'app lo
segnala e resta utilizzabile inserendo il valore a mano.

**Allestimenti con due valorizzazioni.** Nel listino attuale 21 allestimenti su
121 compaiono con due importi diversi (rapporti esatti di 5/3 o 1,25: sono le
aliquote dei due regimi fiscali, 2025 e 2024 transitorio). In quel caso l'app non
sceglie per te: mostra i due valori e propone il più alto, perché un valore
sottostimato produrrebbe una trattenuta insufficiente. Il valore corretto è nella
lettera di assegnazione.

### Cosa calcola

1. **Totale dei fringe benefit dell'anno**

   ```
   totale = fringe auto già in busta paga
          + fringe auto lordo del periodo residuo   (annuo × giorni possesso / giorni anno)
          + IPM + infortuni
          − trattenuta annua × mesi residui / 12
   ```

2. **Imponibile e tassazione.** La soglia di esenzione è uno *scalino*: appena il
   totale la supera, l'imponibile è l'**intero** valore dei fringe benefit, non la
   sola eccedenza. In questa versione beta si assume che il **50%**
   dell'imponibile si traduca in tasse e contributi.

3. **Trattenuta consigliata.** La trattenuta annua minima che porta il totale
   almeno **50 € sotto** il limite, arrotondata all'euro superiore:

   ```
   trattenuta = (totale_lordo − limite + 50) × 12 / mesi_residui
   ```

4. **Test di convenienza.** La trattenuta personalizzata viene proposta solo se il
   costo totale (trattenuta versata + tassazione) è inferiore a quello dello
   scenario base. Altrimenti l'app propone la **trattenuta base di 140 € annui**,
   come previsto dalle specifiche.

### Cosa restituisce

- Quattro KPI: fringe benefit annui stimati, trattenuta consigliata (annua e
  mensile), tasse e contributi stimati, risparmio rispetto alla trattenuta base.
- Un verdetto in chiaro, con quattro esiti possibili: già sotto il limite,
  trattenuta consigliata, non conviene personalizzare, limite non raggiungibile
  con la sola trattenuta.
- Un grafico del costo totale annuo per ogni livello di trattenuta, con il punto
  consigliato evidenziato, e la tabella di confronto degli scenari.
- Il dettaglio voce per voce del calcolo e il riepilogo delle regole applicate.

## Regole implementate

Dalla **«Guida alla tassazione dei fringe benefit»** (aggiornata al 13 febbraio 2026):

- Limite di esenzione 1.000 € annui fino al periodo d'imposta 2027, elevato a
  2.000 € per i lavoratori con figli a carico che lo dichiarano all'azienda.
- Superata la soglia, assoggettamento dell'intero valore dei fringe benefit,
  compresi i mesi pregressi.
- Fringe auto ad uso promiscuo = costo chilometrico ACI × 15.000 km convenzionali
  × aliquota di legge, meno la trattenuta per uso privato.
  - Regime dal 2025: 10% elettriche, 20% plug-in hybrid, 50% altre motorizzazioni.
  - Regime 2024 (transitorio): 25 / 30 / 50 / 60% per fasce di emissioni CO2.
  - +5% dell'aliquota con accessori o allestimenti non valorizzati nelle tabelle ACI.
  - +50% dell'aliquota dopo il quinto anno dalla prima immatricolazione.
- Valorizzazione proporzionale ai giorni di possesso effettivo dell'auto.
- Assicurazioni 2026: IPM 103,39 € (145,93 € dirigenti); infortuni
  extraprofessionali = retribuzione annua soggetta a INPS × 0,246456% − 7,28 € per
  il personale non dirigenziale.

Dal documento **«Progetto uso privato self service»**:

- Margine di sicurezza di almeno 50 € sotto il limite.
- Trattenuta base di 140 € annui quando personalizzarla non conviene.
- Possibilità di variare la trattenuta in corso d'anno, ad esempio al cambio auto.

## Perimetro

Il simulatore considera **auto ad uso promiscuo, assicurazione IPM e
assicurazione infortuni extraprofessionali**.

Sono esclusi i buoni e i rimborsi della piattaforma welfare, perché chi ha l'auto
ad uso promiscuo non può fruirne. Non è incluso l'ESPP: se in futuro serve, è una
riga da aggiungere a `fringe_assicurazioni` in `app.py`.

## Brand

Palette e tipografia sono ripresi dal tema della presentazione sorgente
(`ppt/theme/theme1.xml`):

| Elemento | Valore |
|---|---|
| Rosso primario | `#D30B1C` |
| Navy (testo, sidebar) | `#20313D` |
| Teal | `#65AAA7` |
| Giallo | `#F6C900` |
| Corallo | `#E75D46` |
| Grigi | `#53565A` · `#D0D0CE` |
| Font | `Effra Corp`, con fallback Figtree (Google Fonts) e Arial |

Tutto il tema vive in `.streamlit/config.toml`, senza CSS custom. La dark mode ha
step propri (`#BBD4D3` / `#E75D46`) perché il navy non ha contrasto sufficiente
su fondo scuro.

Per aggiungere il logo aziendale, salva un `logo.png` nella cartella dell'app:
viene rilevato e mostrato automaticamente.

## File

```
SottoSoglia/
├── app.py                   simulatore (motore di calcolo + interfaccia)
├── lista_Auto.xlsx          listino auto: marca, modello, allestimento, fringe annuo
├── requirements.txt
├── launch_app.bat           avvio con doppio clic su Windows
├── README.md
└── .streamlit/config.toml   tema brand
```

## Avvertenza

Versione beta a fini orientativi. La tassazione effettiva dipende dalla
situazione fiscale complessiva del dipendente (detrazioni, trattamento
integrativo, altri elementi retributivi del mese) e le tabelle ACI vengono
aggiornate periodicamente in corso d'anno. Per il proprio caso specifico fa fede
il team HR.
