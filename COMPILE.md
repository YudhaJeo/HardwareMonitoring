# Panduan Build Aplikasi

## 1. Kompilasi Executable (PowerShell)
Jalankan perintah berikut pada terminal PowerShell Anda untuk memaketkan skrip Python:

```powershell
pyinstaller hardware_monitor.spec --distpath versions --workpath build --noconfirm
```

## 2. Pembuatan Installer (Inno Setup)
Buka aplikasi **Inno Setup Compiler**, lalu ikuti langkah-langkah di bawah ini:

1. Pilih menu **File** > **Open**
2. Pilih file `FckinMonitoring.iss`
3. Pilih menu **Build** > **Compile**

## Debug atau Cek Log
```powershell
%LOCALAPPDATA%\FckinMonitoring\fckinmonitoring.log
```

