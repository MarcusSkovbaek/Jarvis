# Sætradar

Holder øje med YouTube og SoundCloud og viser nye DJ-sæt fra de kunstnere, du
følger. Et sæt kommer kun med, når det

1. nævner kunstneren i titlen, uanset stavemåde (eller er uploadet af
   kunstneren selv),
2. er udgivet efter startdatoen og varer **over 30 minutter**, og
3. har **god lyd**: mindst 60/100 i lydvurderingen.

Hvert sæt har et direkte link. Når du åbner det, bliver det markeret som
**Hørt**. Du kan også markere og afmarkere selv, og fortryde.

Sætradaren er sin egen lille app og har intet med Jarvis at gøre. Den kører på
GitHub (ikke på din pc) og kontakter internettet, hvilket Jarvis bevidst aldrig
gør.

**Adresse, når den er sat op:** https://marcusskovbaek.github.io/Jarvis/
(Eksempel på hvordan fund ser ud: https://marcusskovbaek.github.io/Jarvis/#demo)

---

## Opsætning (én gang)

1. **Slå GitHub Pages til.** På GitHub: *Settings → Pages → Build and
   deployment → Source:* vælg **GitHub Actions**.
2. **Start første scanning.** *Actions → Sætradar → Run workflow*. Herefter
   kører den af sig selv hver anden time. Den første kørsel noterer alt, der
   allerede findes, så kun nye uploads bliver vist.
3. **Anbefalet: en YouTube-nøgle.** YouTube lader GitHubs servere søge, men
   afviser dem på selve videosiderne ("Sign in to confirm you're not a bot").
   Uden nøgle kender Sætradaren derfor kun den omtrentlige alder fra
   søgeresultatet ("for 3 dage siden"), og lyden på YouTube kan ikke måles.
   Med en gratis nøgle til YouTube Data API v3 får den præcise
   udgivelsestider:
   - Opret en nøgle på https://console.cloud.google.com/apis/credentials
     (aktivér først *YouTube Data API v3* for projektet).
   - Læg den i repoet under *Settings → Secrets and variables → Actions →
     New repository secret* med navnet `YOUTUBE_API_KEY`.

   Kvoten rækker: seks søgninger hver anden time bruger ca. 7.300 af de
   10.000 enheder, Google giver om dagen (se **Stavemåder** nedenfor).

Fejler en kilde, står det under **Kilder ved seneste tjek** nederst på siden,
og lysdioden i toppen bliver gul eller rød.

### Vedligehold

Under *Actions → Sætradar → Run workflow* kan du også:

- **probe**: indsæt et eller flere links til sæt. De bliver hentet og vurderet
  fra ende til anden, og resultatet står i kørslens oversigt. Brug det til at
  se, om download og lydmåling stadig virker fra GitHub, eller hvorfor et sæt
  blev frasorteret.
- **reset**: glemmer alle gemte fund og tager en ny baseline. Hørt-markeringer
  i dine browsere bliver ikke rørt.

Avanceret og valgfrit: lyden på YouTube kan måles, hvis yt-dlp får cookies fra
en YouTube-konto (secret `YTDLP_COOKIES` med indholdet af en `cookies.txt`).
Brug i så fald en konto, du kan undvære: YouTube kan spærre konti, der bruges
fra servere.

### På iPhone

Åbn adressen i Safari → **Del** → **Føj til hjemmeskærm**. Så åbner den som en
app i fuld skærm, med eget ikon. Hvert link går direkte til sættet på YouTube
eller SoundCloud.

### På Windows (Edge)

Åbn adressen. Vil du have den som app: **⋯ → Apps → Installer dette websted
som en app**.

> Hørt-markeringer gemmes i den browser, du bruger. Din iPhone og din pc
> husker hver for sig.

---

## Sådan vurderes lyden

Scoren starter på 62 og flyttes op og ned af det, der bliver fundet. Under
**Lyd**-måleren på hvert sæt kan du se præcis hvilke fund, der talte.

| Hvad | Effekt |
|---|---|
| Uploadet af kunstneren selv eller en kendt platform (Boiler Room, NTS, HÖR, The Lot Radio, …) | +12 |
| Uploadet af en verificeret YouTube-kanal (fx en festival) | +6 |
| Lyd helt op til 15,5 kHz eller mere | +12 |
| Fyldig bas | +3 |
| God/høj bitrate | +3 / +4 |
| Titlen siger "phone", "crowd recording", "snippet", "interview", … | −30 til −45 |
| Kunstneren nævnes kun efter "feat." i et mix eller en lang navneliste (en anden DJ's mix) | −45 |
| Lyden stopper under 10 kHz (mudret) / under 13 kHz | −40 / −15 |
| Svag bas (typisk telefon i et lokale) | −15 |
| Digital forvrængning (clipping) | −8 til −25 |
| Lange stille passager, meget lav lydstyrke, mono | −6 til −30 |

Lyden måles på to udsnit à 45 sekunder fra midten af sættet (30 % og 65 %
inde). Kan udsnittene ikke hentes (typisk på YouTube, se ovenfor), vurderes
sættet på uploader, bitrate og titel og står som **ikke lydmålt**. Så kræves
der også en uploader, man kan stole på: kunstneren selv, en kendt platform
eller en verificeret kanal. Ellers lander det under **Frasorteret** med den
begrundelse. Vil du altid have en bestemt kanal med, så tilføj den til
`trustedUploaders` i `config/artists.json`.

Genuploads af ældre sæt bliver sorteret fra. Lægges samme sæt både på YouTube
og SoundCloud, vises det én gang med et "Også på"-link.

Alt det, der blev fundet men ikke levede op til kravene, ligger under
**Frasorteret** med begrundelsen. Udelukker titlen alene et fund (et
interview, et klip, en anden DJ's mix), bliver lyden ikke målt, og et sæt,
der blev godkendt før en titelregel fandtes, bliver vurderet igen.

### Navne, der også er almindelige ord

Hedder kunstneren noget, der også er et almindeligt ord (fx **WORSHIP**,
der ellers fanger lovsang og gudstjenester), har hver kunstner to valgfrie
filtre under **Undgå forkerte fund** i appen:

- **Skal også nævne** (`mustMention`): et fund tæller kun, hvis titlen eller
  uploaderen også nævner ét af ordene, fx `Sub Focus, Dimension, drum and
  bass`. Kunstnerens egne profiler og kendte platforme tæller altid.
- **Udelad titler med** (`exclude`): fund, hvis titel nævner et af ordene,
  springes over, fx `praise, prayer, church`.

Ordene matches som hele ord uden hensyn til store/små bogstaver, og "and" og
"&" regnes for det samme. Ændres filtrene, forsvinder sæt, de nu udelukker,
og fund, de før sprang over, bliver vurderet igen. En ekstra søgning som
"WORSHIP drum and bass" under **Andre stavemåder og søgninger** hjælper
desuden med at finde de rigtige sæt blandt alle de andre.

---

## Stavemåder

Kunstnere skriver tit deres navn med specialtegn, og uploadere skriver det på
begge måder. Yousuke Yukimatsu hedder officielt **¥ØU$UK€ ¥UK1MAT$U**, mange
skriver **YØU$UK€ YUK1MAT$U** eller **Yousuke Yukimatsu**, og på japansk
**行松陽介**. YouTube og SoundCloud søger på tegnene, som de står, så hver
stavemåde finder uploads, de andre overser.

Derfor har hver kunstner en liste `searchNames`. Hver stavemåde på listen
bliver søgt på **både YouTube og SoundCloud** ved hver scanning, og titler
med en hvilken som helst af dem tæller som et match. Siden viser listen under
**Søger efter**: tryk på en stavemåde for at åbne samme søgning på YouTube
eller SoundCloud. Under **Kilder ved seneste tjek** nederst på siden er hver
søgning et link til søgningen på platformen. Genkendelsen i titler tager desuden selv højde for
store/små bogstaver, fuldbredde-tegn og "leetspeak" (`¥` for Y, `$` for S,
`1` for I, `€` for E), så også fx "Yousuke Yuk1matsu" bliver fundet.

Med en YouTube-nøgle koster hver YouTube-søgning 100 af de 10.000 daglige
enheder. Seks søgninger hver anden time bruger ca. 7.300. Slipper kvoten op,
bruger Sætradaren automatisk YouTubes almindelige søgeside resten af dagen.

## Overvågninger i appen

Tryk på knappen med skyderne i toppen af siden (**Overvågninger**). Uden
forbindelse til GitHub kan du se, hvad der overvåges og fra hvilken dato. For
at ændre noget skal appen have lov at gemme i repoet:

1. Tryk **Forbind GitHub** og følg vejledningen: lav et *fine-grained* token
   på https://github.com/settings/personal-access-tokens/new, giv det kun
   adgang til **Jarvis**, og sæt **Contents** og **Actions** til **Read and
   write**.
2. Sæt tokenet ind. Det gemmes kun i den browser, du bruger, og sendes kun til
   GitHub. Det kan slettes igen med **Afbryd**, og helt trækkes tilbage på
   GitHubs tokenside.

Derefter kan du:

- **Tilføje en kunstner**: navn med almindelige bogstaver, navnet som
  kunstneren selv skriver det (med specialtegn), flere stavemåder, en dato at
  søge fra og eventuelt SoundCloud-profil og YouTube-kanal.
- **Rette en kunstner**: alt det samme, og **Fjern overvågning** (to tryk).
- **Flytte datoen**, der søges fra: tryk på **Udgivet efter** i toppen af siden
  eller **Ret** i listen. Hurtigvalg: *Fra nu*, *7 dage*, *30 dage*, *1 år*.
  Flyttes datoen tilbage, søger næste scanning længere tilbage (op til 150
  resultater pr. søgning) og vurderer de ældre uploads. Flyttes den frem,
  forsvinder sæt fra før den nye dato. En scanning bruger højst 15 minutter
  på at vurdere uploads (`judgeBudgetMinutes`); er der flere, tager de
  næste scanninger resten, nyeste først, og siden skriver *Søger stadig
  længere tilbage* imens.
- **Scan nu**: starter en scanning på GitHub med det samme. Den findes også
  nederst på siden ved **Kilder**, og når appen er forbundet, gør knappen
  ↻ i toppen det samme. Kører der allerede en scanning, sættes den nye i kø
  og starter bagefter.

Hver ændring gemmes som en ændring af `config/artists.json` i repoet. Det
starter en scanning af sig selv, og appen viser, hvordan den går
(*I kø*, *Scanner*, *Opdateret*). Efter et par minutter står de nye data på
siden. Gemmer to enheder samtidig, læser appen filen igen og lægger sin
ændring oven i den anden.

Den side, der er online, bliver kun skiftet ud, når alle browser-selvtests er
bestået. Fejler de, bliver den forrige version liggende, så appen altid kan
åbnes.

## Tilføj en kunstner i filen

Det samme kan gøres direkte i [`config/artists.json`](config/artists.json).
Tilføj et objekt mere i `artists`:

```json
{
  "id": "dj-eksempel",
  "name": "DJ Eksempel",
  "displayName": "DJ EKSEMPEL",
  "subtitle": "DJ Eksempel · Berlin",
  "trackingSince": "2026-11-01T00:00:00+01:00",
  "searchNames": ["DJ Eksempel", "D.J. €K$€MP€L"],
  "aliases": ["eksempel"],
  "links": {
    "soundcloud": "https://soundcloud.com/djeksempel",
    "residentAdvisor": "https://ra.co/dj/djeksempel"
  },
  "sources": {
    "youtube": {
      "searchQueries": ["DJ Eksempel DJ set"],
      "channels": []
    },
    "soundcloud": {
      "searchQueries": [],
      "users": ["djeksempel"]
    }
  }
}
```

- `id`: kort og unik, kun små bogstaver og bindestreger.
- `trackingSince`: kun sæt udgivet efter dette tidspunkt bliver vist.
- `searchNames`: alle stavemåder af navnet, både med almindelige bogstaver og
  med kunstnerens egne specialtegn. Hver af dem søges på alle platforme.
- `aliases`: ekstra navne, der kun bruges til at genkende titler, fx efternavnet
  alene. Store/små bogstaver, accenter, fuldbredde-tegn og "leetspeak" er der
  allerede taget højde for.
- `sources.*.searchQueries`: ekstra søgninger kun på den ene platform, fx
  `"DJ Eksempel DJ set"`.
- `sources.soundcloud.users`: kunstnerens egne profiler. Uploads derfra tæller
  med, også når titlen ikke nævner navnet, og får plus i lydvurderingen.
- `sources.youtube.channels`: valgfrit, fx `"@boilerroom"`, hvis en kanal skal
  gennemgås direkte.
- `ownAccounts` (valgfri): andre konti, der hører til kunstneren, fx
  `"youtube.com/@kunstner"`.
- `mustMention` og `exclude` (valgfri): filtre for navne, der også er
  almindelige ord. Se [Navne, der også er almindelige ord](#navne-der-også-er-almindelige-ord).

Når der er flere kunstnere, får siden et kunstnerfilter. Fælles indstillinger
(minimumslængde, minimumsscore, kendte platforme) står øverst i samme fil. Er
en kunstner skrevet forkert ind, bliver den sprunget over med en besked øverst
på siden, mens de andre scannes som normalt.

---

## Hvordan det hænger sammen

```
config/artists.json        hvem der følges, og hvordan
tracker/                   scanneren (Python): søger, vurderer, skriver data
  sources.py               YouTube (API eller yt-dlp) og SoundCloud (yt-dlp)
  matching.py              navnegenkendelse, titelord, dubletter
  quality.py               lydmåling (ffmpeg + numpy) og scoren
  pipeline.py              selve kørslen og filerne
web/                       siden: index.html, assets/, data/
  assets/app.js            visningen af sæt, hørt-markeringer, filtre
  assets/admin.js          "Overvågninger": ændringer gemmes via GitHubs API
tests/                     selvtests for scanner og side
  fixtures/artists.json    fast kopi af opsætningen, som testene bruger
.github/workflows/set-tracker.yml   kører scanneren hver anden time
```

Workflowet gemmer sine data på grenen `set-tracker-data` (så Jarvis' historik
ikke fyldes op) og udgiver `web/` sammen med de nyeste data til GitHub Pages.

### Køre lokalt

```bash
cd set-tracker
pip install -r requirements.txt          # yt-dlp og numpy; ffmpeg skal også være installeret
python -m unittest discover -s tests     # selvtests for scanneren
python -m tracker                        # én scanning; skriver web/data/
python -m http.server 8766 --directory web
```

Åbn http://127.0.0.1:8766/ (eller `#demo` for eksempeldata).
`web/index.html` kan også åbnes direkte fra disken; så hentes de nyeste data
fra grenen `set-tracker-data`.

Browser-selvtestene kører siden i Chromium i iPhone- og desktopstørrelse, lyst
og mørkt tema, og fejler ved scriptfejl, vandret scroll, afklippet tekst,
elementer der stikker ud, eller knapper der ikke gør det, de siger.
`tests/ui_check.mjs` dækker visningen af sæt, `tests/ui_admin.mjs` dækker
**Overvågninger** mod en stand-in for GitHubs API (tokens, konflikter,
manglende rettigheder, kørsler) og tjekker præcis, hvad der skrives til
opsætningsfilen:

```bash
npm i playwright && node tests/ui_check.mjs && node tests/ui_admin.mjs
```

Testene bruger en fast kopi af opsætningen (`tests/fixtures/artists.json`),
så ændringer i appen aldrig kan få dem til at fejle.
