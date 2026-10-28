# OpenClip Local

Clipper video lokal untuk laptop Bagas. Dibuat berdasarkan alur yang ditelusuri dengan skill dan MCP REA pada OpusClip: input video, transkrip, kurasi kandidat, timeline edit, render, lalu ekspor. Kode dan mesin AI di sini merupakan implementasi mandiri.

## Pakai sekarang

Buka **http://127.0.0.1:7860** saat server berjalan.

Untuk menjalankan lagi:

```bash
cd /home/bagas/Documents/Codex/2026-10-23/gun/outputs/openclip
make dev
```

`make dev` menyiapkan virtualenv/dependensi bila diperlukan, lalu menjalankan satu server di loopback. Ctrl+C menghentikan server. Dari terminal lain, `make stop` menghentikan server milik folder ini setelah memeriksa PID dan direktori proses. `make open` membuka browser.

1. Simpan API key dan model OpenRouter di **Pengaturan AI**. Pilih **Proyek baru** dan unggah video, atau masukkan URL sumber publik.
2. Pilih topik, rentang durasi klip, jumlah klip, format, dan komposisi.
3. Tekan **Buat klip**. Tunggu transkripsi, kurasi, dan render selesai.
4. Putar hasil, unduh MP4, subtitle SRT, atau timeline JSON. Buka **Atur potongan dan posisi** untuk mengganti rentang sumber dan posisi crop, lalu render ulang.

Proyek dan hasil disimpan pada `data/projects/<id>/`. Setelah server dinyalakan ulang, proyek lama tetap ada. Job yang terputus ditandai dan dapat dilanjutkan dari UI; transkrip yang selesai digunakan kembali. Setiap render menghasilkan versi file baru.

### Jika YouTube meminta verifikasi login

1. Pilih proyek yang gagal, lalu klik **Login YouTube** di bagian atas dashboard atau di dekat pesan error.
2. Pilih browser yang terpasang, lalu **1. Buka login YouTube**. Login sendiri pada situs YouTube/Google di jendela browser khusus OpenClip; pastikan video sumber bisa diputar.
3. Tutup jendela browser itu, kembali ke OpenClip, lalu **2. Sudah login · coba lagi**. Tombol ini menyimpan pilihan sesi dan melanjutkan proyek yang gagal. Untuk proyek baru, **Gunakan sesi** menyiapkan pengaturan sumber; masukkan tautan lalu pilih **Buat klip**.
4. Pilihan **Gunakan file cookies.txt** pada dialog langsung menampilkan kontrol unggah. Cookie harus berformat Netscape, maksimal 2 MB. Pilih file, **Simpan akses YouTube**, lalu **Lanjutkan / coba lagi**. Ikuti [panduan resmi ekspor cookie YouTube](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies); perbarui file bila sesi kedaluwarsa. Unggah video langsung tetap tersedia jika YouTube menolak akses.

Login memakai profil khusus di `data/youtube-browser/<browser>/`, dalam folder privat `0700`. Downloader membaca profil yang sama saat pengguna menjalankan unduhan, menyaring cookie untuk domain YouTube, dan tidak mengekspor cookie browser. OpenClip tidak menerima password atau menyatakan akun sudah terverifikasi hanya karena browser dijalankan. Sesi profil browser tetap tersimpan dan bisa digunakan beberapa proyek; menghapus proyek tidak mengeluarkan akun dari profil bersama ini. Untuk keluar, buka kembali jendela login dan gunakan menu akun YouTube. Ini memakai sesi browser, bukan OAuth yt-dlp yang sudah tidak didukung.

Pilihan browser biasa di **Akses YouTube** tetap tersedia untuk profil yang dipilih otomatis oleh yt-dlp. SecretStorage mendukung pembacaan keyring Linux hanya pada jalur unduh yang dipilih pengguna. File cookie unggahan disaring untuk YouTube, disimpan terpisah di `data/youtube-sessions/` dengan folder `0700` dan file `0600`, serta tidak disertakan dalam JSON proyek, log, hasil ekspor, atau request OpenRouter. Pilih **Tanpa sesi login** lalu simpan untuk menghapus file cookie proyek; penghapusan proyek juga menghapus file tersebut. Cookie berisi sesi login, jadi jangan dibagikan.

**Requested format is not available** berbeda dari permintaan login: stream video/audio yang cocok belum terbaca. Dependensi kini memakai `yt-dlp[default]` agar EJS yang kompatibel ikut terpasang, dan downloader mengaktifkan Node atau Deno yang tersedia. Node.js minimal 22 atau Deno minimal 2.3 diperlukan untuk sumber YouTube; konfigurasi mengikuti [panduan EJS yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/EJS). Peringatan downloader diringkas sebagai kode diagnostik tanpa menyimpan isi cookie. Login tidak menjamin semua video atau format tersedia.

## Yang tersedia

- Unggah MP4/MOV/MKV/WEBM/AVI/M4V sesuai ruang disk, durasi maksimal 2 jam.
- Impor URL memakai yt-dlp dengan pilihan sumber hingga 4K, 1080p, atau 720p. File lebih dari 1 GB didukung; pemilihan resolusi tidak dibatasi ukuran file. Unduhan dan penggabungan audio/video diperiksa terhadap ruang disk yang tersedia, dengan cadangan 512 MB. File unduhan yang cocok dapat dilanjutkan setelah terputus. Situs tertentu dapat meminta login atau menolak unduhan; unggah file tetap tersedia.
- Subtitle melalui STT OpenRouter dan kurasi/judul melalui Chat Completions OpenRouter. Model diatur pada **Pengaturan AI**; pilihan model lokal tidak tersedia pada alur aplikasi.
- SRT milik pengguna dapat menggantikan transkripsi. Timestamp kata dalam mode ini merupakan perkiraan dari blok subtitle.
- Kurasi dari transkrip: kandidat bertimestamp, preferensi topik, skor editorial, dan pembatasan tumpang tindih.
- Kandidat bertimestamp disiapkan dari transkrip, kemudian dinilai dan diberi judul oleh OpenRouter.
- Reframe 9:16, 1:1, atau 16:9. Mode **Ikuti pembicara (lokal)** melacak hingga enam wajah pada enam sampel per detik, memperkirakan pembicara dari perubahan bentuk bibir ketika ada ujaran, dan menghaluskan gerak crop. Mode fit mempertahankan seluruh gambar dengan padding.
- Subtitle karaoke ASS yang dibakar ke MP4, SRT terpisah, dan timeline JSON.
- Antrean satu worker, progress, pembatalan, coba lagi, pustaka proyek, pemutaran sumber, transkrip yang dapat diklik, dan render ulang.

## Pemrosesan AI dan biaya

Subtitle dan kurasi selalu memakai OpenRouter. Default model subtitle adalah `openai/whisper-large-v3`, sedangkan kurasi/judul memakai `google/gemini-2.5-flash`; model lain dapat diatur pada Pengaturan AI. API key disimpan di laptop dan tidak dikembalikan ke browser.

Pengaturan budget manual dan batas biaya per proyek telah dihapus. Request mengikuti isi/durasi video serta pemakaian model yang diperlukan. Biaya dari respons OpenRouter dijumlahkan pada proyek; bila respons tidak menyertakan biaya, estimasi dari katalog dipakai sebagai catatan. Biaya tercatat merupakan informasi, tidak menghentikan pemrosesan pada nominal tertentu.

Skor menunjukkan prioritas editorial dari model, bukan peluang viral. Topik menjadi arahan pemilihan; hasil tetap perlu ditinjau. Pelacakan kamera dan render berjalan di laptop.

## Batas versi ini

### Pembaruan sesuai acuan pengguna

- OpenRouter tersedia untuk dua tugas terpisah: subtitle melalui endpoint STT (default `openai/whisper-large-v3`), serta kurasi/judul melalui Chat Completions (default `google/gemini-2.5-flash`). API key dimasukkan di **Pengaturan AI** dan tidak ditampilkan kembali.
- Pada proyek lama, buka **Perbaiki subtitle dan kurasi**, tentukan rentang durasi, kemudian jalankan proses ulang dengan OpenRouter. Hasil lama tidak otomatis berubah ketika API key disimpan. Konfigurasi proyek lama disesuaikan ke OpenRouter saat server dimuat, sementara transkrip dan hasil yang sudah ada tetap disimpan.
- Provider STT kadang mengembalikan timestamp segmen tanpa timestamp kata meskipun model dan request sama. Bagian tersebut tetap ditampilkan menurut waktu segmen dari provider, dengan teks putih tanpa highlight kata. Bagian yang memiliki timestamp kata tetap memakai subtitle karaoke. Mode campuran dan catatan keterbatasannya disimpan pada proyek dan timeline; waktu kata tidak dibuat-buat dari durasi segmen. Ini mengikuti variasi keluaran terstruktur yang dijelaskan pada [dokumentasi STT OpenRouter](https://openrouter.ai/docs/guides/overview/multimodal/stt).
- Respons transkripsi setiap bagian disimpan sebelum validasi. Jika proyek terhenti lalu **Lanjutkan / coba lagi** dipilih dengan model dan bahasa yang sama, respons tersimpan dipakai kembali. Pada kasus lima cache lengkap, transkripsi tidak dikirim ulang; kurasi OpenRouter yang belum dijalankan tetap memakai request dan biaya normal.
- Preset baru: 1080×1920 untuk vertikal, rentang awal 45–180 detik (batas 240 detik), teks kapital tebal dengan outline hitam dan kata aktif kuning. Font Luckiest Guy beserta lisensinya dibundel lokal. Aplikasi tidak menambahkan watermark.
- Tersedia **Simpan hasil ke folder**, **Buka folder hasil**, dan **Unduh semua ZIP**, selain unduhan MP4/SRT per klip.
- STT dipecah menjadi bagian dengan cache respons agar hasil yang selesai dapat dipakai kembali. Biaya mengikuti pemakaian provider, tanpa pengaturan batas nominal pada aplikasi.
- Pengujian berikutnya diserahkan kepada pengguna sesuai permintaan. Penggunaan OpenRouter dengan key pengguna belum diuji atau dijalankan oleh agen.

### Kualitas gambar

Unduhan awal pernah dibatasi 720p untuk laptop. Pada sumber lanskap 1280×720, crop vertikal 9:16 hanya mengambil sekitar 405×720 piksel; ekspor 1080×1920 memperbesar area tersebut 2.67 kali. Mengganti ukuran ekspor saja tidak memulihkan detail sumber.

Proyek baru memilih sumber hingga 4K secara default, diikuti pilihan 1080p atau 720p. Pemilihan yt-dlp mengutamakan SDR, kemudian resolusi, dengan preferensi H.264 pada resolusi setara. Tidak ada filter ukuran atau batas unduhan 1 GB. Ruang sisa diperiksa selama unduhan, serta sebelum penggabungan yang membutuhkan ruang tambahan untuk MP4 akhir. Jika ruang kurang, pesan memuat kapasitas tersedia dan kebutuhan minimal; kosongkan ruang lalu pilih **Lanjutkan / coba lagi**. Sumber yang tidak tersedia pada resolusi tinggi memakai kualitas tersedia. [Pemilihan format yt-dlp](https://github.com/yt-dlp/yt-dlp#format-selection) menjadi acuan selector.

Ekspor **Tinggi** memakai H.264 CRF 18/preset medium, scaler Lanczos, dan AAC 192 kbps. Pilihan **Ringan** mempertahankan CRF 23/preset veryfast dan AAC 128 kbps. Render kualitas tinggi memerlukan lebih banyak waktu dan biasanya menghasilkan file lebih besar. Tidak ada penajaman atau pemulihan detail AI yang diterapkan.

Untuk proyek yang sudah selesai, buka **Perbaiki kualitas gambar**, aktifkan **Unduh ulang sumber resolusi lebih tinggi** bila memakai tautan, lalu klik **Render ulang kualitas tinggi**. Jalur ini mempertahankan judul, rentang, subtitle, serta pilihan kamera, dan tidak menjalankan ulang STT atau kurasi. Unduhan baru memakai nama berbeda; sumber lama dipertahankan. Durasi dan keberadaan audio diperiksa sebelum sumber baru digunakan agar timestamp tidak bergeser. Sumber baru dengan resolusi lebih rendah tidak menggantikan sumber lama. Pada proyek unggahan, render memakai file asli yang tersedia.

Operasi kualitas yang terputus dapat dilanjutkan dengan tombol **Lanjutkan / coba lagi**. Jika unduhan sumber baru sudah selesai, sumber itu dipakai kembali. Area crop sumber, faktor pembesaran, dan profil encoding dicatat dalam timeline. Catatan ditampilkan bila crop masih diperbesar cukup jauh; detail akhir tetap mengikuti sumber.

### Kamera mengikuti pembicara

Pada proyek yang sudah selesai, klik **Render ulang kamera · ikuti pembicara**. Tombol ini mempertahankan rentang, judul, dan transkrip semua klip, lalu membuat versi render baru. Jalur ini tidak memanggil STT atau kurasi OpenRouter. Hasil sebelumnya tetap tersedia bila render baru gagal. Pembatalan dapat dilanjutkan dengan operasi kamera yang sama.

Pada pengaturan per klip, pilihan **Kamera** menyediakan **Ikuti pembicara (lokal)**, **Posisi manual**, dan **Tampilkan seluruh gambar**. Mengubah judul atau rentang tidak lagi otomatis mematikan tracking; slider crop hanya berlaku saat memilih posisi manual.

Model landmark [MediaPipe Face Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/python) diunduh saat setup pertama (sekitar 3.8 MB, versi dan SHA-256 dipin di `openclip/tracking.py`). Video untuk pelacakan tetap di laptop. Identitas track ditautkan antar-frame menggunakan posisi dan ukuran wajah. Perubahan landmark bibir, energi audio, dan waktu ujaran digunakan oleh heuristik lokal untuk memperkirakan pembicara. Skor memakai jendela pendek dengan konteks sebelum/sesudah frame, ambang pergantian, dan jeda minimum untuk mengurangi perpindahan karena gerak kecil. Perpindahan crop diinterpolasi per frame oleh FFmpeg; batas shot menahan pan melewati potongan kamera.

Pemilihan pembicara masih berupa estimasi. Gerakan bibir yang tertutup, wajah kecil, pembicara di luar gambar, atau orang yang berbicara bersamaan dapat membuat target ambigu. Kamera menahan target ketika bukti pergantian lemah atau target hilang sementara. Bila sama sekali tidak ada wajah terdeteksi, crop tengah dipakai dan catatan ditampilkan. Statistik, ID track sementara, dan titik gerak kamera tersedia di timeline JSON. Kualitas tracking baru belum diuji pada video pengguna; pengujian diserahkan kepada pengguna sesuai permintaan.

Fokusnya konten dengan ujaran, seperti podcast, wawancara, atau penjelasan. Belum ada pemahaman visual multimodal seperti klaim ClipAnything, diarization identitas suara, pemilihan layout multi-speaker, B-roll generatif, penyambungan beberapa bagian jauh menjadi satu cerita, auto-import channel, atau posting ke media sosial. Kualitas model internal OpusClip dan bobot skornya tidak diketahui.

Video unggahan dan transkrip tidak dikirim ke OpusClip. Audio/transkrip dikirim ke OpenRouter dan provider model saat pemrosesan subtitle dan kurasi. File sumber, cache, pelacakan kamera, dan render berada di laptop.

## Persyaratan dan validasi

Python 3.12, FFmpeg/ffprobe dengan libass, API key OpenRouter, dan `uv` atau modul venv/pip. Dependensi dipisahkan di `.venv`. Whisper lokal, PyAV, serta Ollama tidak diperlukan untuk alur pemrosesan aplikasi.

```bash
make test
```

Pemetaan bukti REA tersedia di [REA-MAPPING.md](REA-MAPPING.md). Rincian hasil pengujian nyata tersedia di [VALIDATION.md](VALIDATION.md).
