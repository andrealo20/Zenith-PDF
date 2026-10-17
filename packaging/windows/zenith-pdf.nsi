; The Zenith PDF installer for Windows. tools/build.py runs makensis on it with these defines:
;   VERSION  the app's version, such as 0.4.0
;   SOURCE   the folder PyInstaller made, with Zenith-PDF.exe in it
;   OUTFILE  the installer to write
;   ICON     zenith.ico
;   HEADER   the logo at the right of the header, a bitmap
;   SIDE     the picture down the side of the first and last pages, a bitmap
;   LICENSE  the licence text
;   FILES    the Delete and RMDir lines that remove what SOURCE holds
;   SIGN     (optional) a script that signs the file it is given

Unicode true
ManifestDPIAware true
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"

!define APP "Zenith PDF"
!define EXE "Zenith-PDF.exe"
!define PUBLISHER "Andrea Loroni"
!define WEBSITE "https://github.com/andrealo20/Zenith-PDF"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ZenithPDF"
!define PROG_ID "ZenithPDF.Document"
!define REGISTERED "ZenithPDF"
; Windows' Default apps page for Zenith PDF: Windows 11 shows a Set default button at its top
!define DEFAULT_APPS "ms-settings:defaultapps?registeredAppMachine=${REGISTERED}"

!ifdef SIGN
  !finalize '"${SIGN}" "%1"'
  !uninstfinalize '"${SIGN}" "%1"'
!endif

Name "${APP}"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\${APP}"
InstallDirRegKey HKLM "Software\ZenithPDF" "InstallDir"
RequestExecutionLevel admin
BrandingText "${APP} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "${APP} Setup"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "LegalCopyright" "Copyright (C) 2026 ${PUBLISHER}. GPL-3.0-or-later."

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "${HEADER}"
!define MUI_HEADERIMAGE_UNBITMAP "${HEADER}"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${SIDE}"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "${SIDE}"
!define MUI_ABORTWARNING

Var ShortcutWanted
Var ShortcutBox
Var DefaultWanted
Var DefaultBox
Var DefaultAsked

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LICENSE}"
!insertmacro MUI_PAGE_DIRECTORY
Page custom OptionsPage OptionsPageLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Start ${APP}"
!define MUI_FINISHPAGE_LINK "${APP} on GitHub"
!define MUI_FINISHPAGE_LINK_LOCATION "${WEBSITE}"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${APP} needs a 64-bit version of Windows 10 or 11."
    Abort
  ${EndIf}
  SetRegView 64
  StrCpy $ShortcutWanted ${BST_CHECKED}
  ; An update keeps the choice made at the first install.
  ClearErrors
  ReadRegDWORD $0 HKLM "Software\ZenithPDF" "DesktopShortcut"
  ${IfNot} ${Errors}
  ${AndIf} $0 == 0
    StrCpy $ShortcutWanted ${BST_UNCHECKED}
  ${EndIf}
  StrCpy $DefaultWanted ${BST_CHECKED}
  ClearErrors
  ReadRegDWORD $0 HKLM "Software\ZenithPDF" "DefaultPdf"
  ${IfNot} ${Errors}
  ${AndIf} $0 == 0
    StrCpy $DefaultWanted ${BST_UNCHECKED}
  ${EndIf}
  ; Whether the question was put already, here or in the app: then the app doesn't put it again.
  ReadRegDWORD $DefaultAsked HKLM "Software\ZenithPDF" "DefaultAsked"
  ${If} $DefaultAsked == ""
    StrCpy $DefaultAsked 0
  ${EndIf}
FunctionEnd

Function .onInstSuccess
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/RELAUNCH" $1
  ${IfNot} ${Errors}
    ; Through Explorer, so the app starts as the user, without the installer's administrator rights.
    Exec '"$WINDIR\explorer.exe" "$INSTDIR\${EXE}"'
  ${EndIf}
  ; Windows lets no program make itself the default: its own page opens on Zenith PDF, for one click there.
  ; Through Explorer, so it opens as the user. Never in a silent update, which asks nothing.
  ${IfNot} ${Silent}
  ${AndIf} $DefaultWanted == ${BST_CHECKED}
    Exec '"$WINDIR\explorer.exe" "${DEFAULT_APPS}"'
  ${EndIf}
FunctionEnd

Function un.onInit
  SetRegView 64
FunctionEnd

Function OptionsPage
  !insertmacro MUI_HEADER_TEXT "Options" "A desktop shortcut, and the app that opens PDF files."
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateCheckbox} 0 12u 100% 12u "Create a desktop shortcut for ${APP}"
  Pop $ShortcutBox
  ${NSD_SetState} $ShortcutBox $ShortcutWanted
  ${NSD_CreateCheckbox} 0 36u 100% 12u "Make ${APP} the default app for PDF files"
  Pop $DefaultBox
  ${NSD_SetState} $DefaultBox $DefaultWanted
  ${NSD_CreateLabel} 12u 52u 95% 30u "Windows asks you to confirm: when the installation ends, its Default apps \
settings open at ${APP}. Choose Set default there (on Windows 10, pick ${APP} for .pdf files)."
  Pop $0
  nsDialogs::Show
FunctionEnd

Function OptionsPageLeave
  ${NSD_GetState} $ShortcutBox $ShortcutWanted
  ${NSD_GetState} $DefaultBox $DefaultWanted
  StrCpy $DefaultAsked 1
FunctionEnd

Section "Install"
  SetShellVarContext all
  ; An update starts as the app closes: wait, up to 30 seconds, until it has let go of its files.
  StrCpy $1 0
  ${DoWhile} ${FileExists} "$INSTDIR\${EXE}"
    ClearErrors
    FileOpen $0 "$INSTDIR\${EXE}" a
    ${IfNot} ${Errors}
      FileClose $0
      ${Break}
    ${EndIf}
    Sleep 500
    IntOp $1 $1 + 1
    ${If} $1 >= 60
      ${Break}
    ${EndIf}
  ${Loop}
  ; An earlier version goes first, so none of its files are left among the new ones.
  ${If} ${FileExists} "$INSTDIR\Uninstall.exe"
    ExecWait '"$INSTDIR\Uninstall.exe" /S _?=$INSTDIR'
  ${EndIf}

  SetOutPath "$INSTDIR"
  File /r "${SOURCE}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\ZenithPDF" "InstallDir" "$INSTDIR"
  WriteRegDWORD HKLM "Software\ZenithPDF" "DesktopShortcut" $ShortcutWanted
  WriteRegDWORD HKLM "Software\ZenithPDF" "DefaultPdf" $DefaultWanted
  WriteRegDWORD HKLM "Software\ZenithPDF" "DefaultAsked" $DefaultAsked

  CreateShortcut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\${EXE}"
  ${If} $ShortcutWanted == ${BST_CHECKED}
    CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\${EXE}"
  ${EndIf}

  ; Installed apps
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${EXE}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "URLInfoAbout" "${WEBSITE}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "EstimatedSize" "$0"

  ; "Open with" for PDF files; the default app is the user's choice, made in Windows' settings
  ; with the name and logo Windows shows in the list of apps it suggests for a PDF
  WriteRegStr HKLM "Software\Classes\${PROG_ID}" "" "PDF document"
  WriteRegStr HKLM "Software\Classes\${PROG_ID}" "FriendlyTypeName" "PDF document"
  WriteRegStr HKLM "Software\Classes\${PROG_ID}\DefaultIcon" "" "$INSTDIR\${EXE},0"
  WriteRegStr HKLM "Software\Classes\${PROG_ID}\Application" "ApplicationName" "${APP}"
  WriteRegStr HKLM "Software\Classes\${PROG_ID}\Application" "ApplicationIcon" "$INSTDIR\${EXE},0"
  WriteRegStr HKLM "Software\Classes\${PROG_ID}\Application" "ApplicationCompany" "${PUBLISHER}"
  WriteRegStr HKLM "Software\Classes\${PROG_ID}\shell\open" "FriendlyAppName" "${APP}"
  WriteRegStr HKLM "Software\Classes\${PROG_ID}\shell\open\command" "" '"$INSTDIR\${EXE}" "%1"'
  WriteRegStr HKLM "Software\Classes\.pdf\OpenWithProgids" "${PROG_ID}" ""
  WriteRegStr HKLM "Software\Classes\Applications\${EXE}" "FriendlyAppName" "${APP}"
  WriteRegStr HKLM "Software\Classes\Applications\${EXE}\DefaultIcon" "" "$INSTDIR\${EXE},0"
  WriteRegStr HKLM "Software\Classes\Applications\${EXE}\shell\open\command" "" '"$INSTDIR\${EXE}" "%1"'
  WriteRegStr HKLM "Software\Classes\Applications\${EXE}\SupportedTypes" ".pdf" ""
  ; A copy run from source may have written these names for this user, pointing at Python: they would hide
  ; the installed ones. Its own names (ZenithPDF.Source) stay.
  DeleteRegKey HKCU "Software\Classes\${PROG_ID}"
  DeleteRegValue HKCU "Software\Classes\.pdf\OpenWithProgids" "${PROG_ID}"
  DeleteRegKey HKCU "Software\ZenithPDF"
  DeleteRegValue HKCU "Software\RegisteredApplications" "${REGISTERED}"
  DeleteRegKey HKCU "Software\Classes\zenith-pdf"
  ; Listed in Settings > Apps > Default apps, where the user can make it the default
  WriteRegStr HKLM "Software\ZenithPDF\Capabilities" "ApplicationName" "${APP}"
  WriteRegStr HKLM "Software\ZenithPDF\Capabilities" "ApplicationDescription" "Read, mark up and study PDF documents"
  WriteRegStr HKLM "Software\ZenithPDF\Capabilities" "ApplicationIcon" "$INSTDIR\${EXE},0"
  WriteRegStr HKLM "Software\ZenithPDF\Capabilities\FileAssociations" ".pdf" "${PROG_ID}"
  WriteRegStr HKLM "Software\RegisteredApplications" "${REGISTERED}" "Software\ZenithPDF\Capabilities"

  ; zenith-pdf:// links, such as those in the notes sent to Obsidian, open a PDF at a page
  WriteRegStr HKLM "Software\Classes\zenith-pdf" "" "URL:Zenith PDF link"
  WriteRegStr HKLM "Software\Classes\zenith-pdf" "URL Protocol" ""
  WriteRegStr HKLM "Software\Classes\zenith-pdf\DefaultIcon" "" "$INSTDIR\${EXE},0"
  WriteRegStr HKLM "Software\Classes\zenith-pdf\shell\open\command" "" '"$INSTDIR\${EXE}" "%1"'
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
SectionEnd

Section "Uninstall"
  SetShellVarContext all
  Delete "$DESKTOP\${APP}.lnk"
  Delete "$SMPROGRAMS\${APP}.lnk"

  ; Only what the installer put down: the folder may hold files of the user's.
  !include "${FILES}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"

  DeleteRegKey HKLM "${UNINSTALL_KEY}"
  DeleteRegKey HKLM "Software\ZenithPDF"
  DeleteRegValue HKLM "Software\RegisteredApplications" "${REGISTERED}"
  DeleteRegKey HKLM "Software\Classes\${PROG_ID}"
  DeleteRegValue HKLM "Software\Classes\.pdf\OpenWithProgids" "${PROG_ID}"
  DeleteRegKey HKLM "Software\Classes\Applications\${EXE}"
  DeleteRegKey HKLM "Software\Classes\zenith-pdf"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
SectionEnd
