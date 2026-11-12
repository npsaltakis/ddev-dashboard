<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img src="assets/logo-light.svg" alt="DDEV Dashboard" height="64">
  </picture>
</p>

<p align="center">
  Τοπικό web περιβάλλον για τα projects του <a href="https://ddev.com">DDEV</a>: βλέπεις τι τρέχει, κάνεις start/stop,<br>
  φτιάχνεις projects, διαχειρίζεσαι domains, βάσεις και logs, χωρίς εντολές.
</p>

<p align="center">
  <a href="README.md">English</a> · <b>Ελληνικά</b>
</p>

---

## Δυνατότητες

- **Όλα τα projects με μια ματιά**: κατάσταση του καθενός και ζωντανή έξοδος όσο τρέχουν οι εντολές.
- **Start / Stop / Restart** ανά project και **Stop όλα** (`ddev poweroff`).
- **Νέο project**: φάκελος, τύπος, PHP, web server, βάση, επιπλέον domains, προαιρετικά phpMyAdmin.
- **Domains**: προσθήκη και αφαίρεση `*.ddev.site` και δικών σου domains (π.χ. `mysite.test`), με έτοιμη τη γραμμή για το αρχείο hosts.
- **Ρυθμίσεις project**: τύπος, έκδοση PHP, web server, docroot, Xdebug on/off.
- **Βάση**: phpMyAdmin, λήψη backup `.sql.gz`, εισαγωγή dump, snapshots (δημιουργία, επαναφορά, διαγραφή).
- **Logs** των containers web και db.
- **Διαγραφή** project (τα αρχεία μένουν, προαιρετικό snapshot ασφαλείας).
- **Ελληνικά και English**.
- Δουλεύει όπου τρέχει το DDEV: **WSL, Linux, macOS, Windows**.

## Τι χρειάζεται

- [DDEV](https://ddev.readthedocs.io/en/stable/users/install/) εγκατεστημένο και λειτουργικό (να δουλεύει το `ddev list`).
- Python 3.8 ή νεότερη. Καμία επιπλέον βιβλιοθήκη.

Το dashboard τρέχει **στο ίδιο σύστημα με το DDEV**: μέσα στο WSL αν το DDEV είναι στο WSL, στα Windows αν το DDEV είναι εγκατεστημένο στα Windows κ.ο.κ.

## Εγκατάσταση

```bash
git clone https://github.com/npsaltakis/ddev-dashboard.git ~/ddev-dashboard
python3 ~/ddev-dashboard/server.py
```

Σε Windows (DDEV για Windows):

```powershell
git clone https://github.com/npsaltakis/ddev-dashboard.git C:\ddev-dashboard
python C:\ddev-dashboard\server.py
```

Άνοιξε το **http://localhost:8099**. Για άλλο port όρισε το `DDEV_DASHBOARD_PORT`.

## Αυτόματη εκκίνηση

Άνοιξε **⚙ Ρυθμίσεις → Αυτόματη εκκίνηση του dashboard**. Εκεί υπάρχουν έτοιμες εντολές για το σύστημά σου, με τις σωστές διαδρομές και κουμπί αντιγραφής:

| Σύστημα | Τρόπος |
|---|---|
| WSL / Linux | systemd user service `ddev-dashboard` + `loginctl enable-linger`. Στο WSL πρέπει να είναι ενεργό το systemd (`[boot] systemd=true` στο `/etc/wsl.conf`). |
| macOS | LaunchAgent `com.ddev.dashboard` |
| Windows | Scheduled Task «DDEV Dashboard» στη σύνδεση (με `pythonw`, χωρίς παράθυρο) |

Σε WSL / Linux:

```bash
systemctl --user restart ddev-dashboard          # επανεκκίνηση
systemctl --user disable --now ddev-dashboard    # απενεργοποίηση
journalctl --user -u ddev-dashboard -f           # logs του dashboard
```

## Ρυθμίσεις

Οι **⚙ Ρυθμίσεις** αποθηκεύονται στο `settings.json`, δίπλα στο `server.py`:

- **Γλώσσα**: Ελληνικά ή English.
- **Πού τρέχει το DDEV**: εντοπίζεται αυτόματα ή το ορίζεις εσύ. Από αυτό εξαρτάται πώς γράφεις τις διαδρομές (στο WSL γράφεις `D:\Projects\site` και γίνεται `/mnt/d/Projects/site`), ποιο αρχείο hosts προτείνεται και ποιες εντολές αυτόματης εκκίνησης εμφανίζονται.
- **Προεπιλεγμένος φάκελος για νέα projects**: κενό σημαίνει ο φάκελος όπου βρίσκονται τα περισσότερα projects σου.

## Ασφάλεια

Το DDEV Dashboard είναι φτιαγμένο για το δικό σου μηχάνημα.

- Ακούει μόνο στο `127.0.0.1`, οπότε δεν φαίνεται στο δίκτυο.
- Απαντά μόνο σε αιτήματα προς `localhost` (προστασία από DNS rebinding).
- Οι ενέργειες θέλουν ειδικό header, οπότε άλλα sites δεν μπορούν να τις ενεργοποιήσουν από τον browser σου.
- Τα στοιχεία των φορμών (ονόματα, domains, εκδόσεις, docroot) ελέγχονται. Το ddev τρέχει χωρίς shell.

## Δομή

```
server.py      HTTP server και κλήσεις στο ddev (μόνο τυπική βιβλιοθήκη Python)
index.html     όλο το περιβάλλον (χωρίς build)
assets/        λογότυπο, εικονίδιο, social preview
```

## Άδεια

[MIT](LICENSE)

Το DDEV Dashboard είναι ανεξάρτητο project, χωρίς σχέση ή έγκριση από το DDEV Foundation. Το όνομα «DDEV» χρησιμοποιείται μόνο για να περιγράψει με τι συνεργάζεται.
