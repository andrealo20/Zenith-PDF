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

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LICENSE}"
!insertmacro MUI_PAGE_DIRECTORY
Page custom ShortcutPage ShortcutPageLeave
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
FunctionEnd

Function un.onInit
  SetRegView 64
FunctionEnd

Function ShortcutPage
  !insertmacro MUI_HEADER_TEXT "Desktop Shortcut" "Create a shortcut on your desktop."
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateCheckbox} 0 12u 100% 12u "Create a desktop shortcut for ${APP}"
  Pop $ShortcutBox
  ${NSD_SetState} $ShortcutBox $ShortcutWanted
  nsDialogs::Show
FunctionEnd

Function ShortcutPageLeave
  ${NSD_GetState} $ShortcutBox $ShortcutWanted
FunctionEnd

Section "Install"
  SetShellVarContext all
  ; An earlier version goes first, so none of its files are left among the new ones.
  ${If} ${FileExists} "$INSTDIR\Uninstall.exe"
    ExecWait '"$INSTDIR\Uninstall.exe" /S _?=$INSTDIR'
  ${EndIf}

  SetOutPath "$INSTDIR"
  File /r "${SOURCE}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\ZenithPDF" "InstallDir" "$INSTDIR"

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

  ; "Open with" for PDF files; the default app stays the user's choice
  WriteRegStr HKLM "Software\Classes\${PROG_ID}" "" "PDF document"
  WriteRegStr HKLM "Software\Classes\${PROG_ID}\DefaultIcon" "" "$INSTDIR\${EXE},0"
  WriteRegStr HKLM "Software\Classes\${PROG_ID}\shell\open\command" "" '"$INSTDIR\${EXE}" "%1"'
  WriteRegStr HKLM "Software\Classes\.pdf\OpenWithProgids" "${PROG_ID}" ""
  WriteRegStr HKLM "Software\Classes\Applications\${EXE}\shell\open\command" "" '"$INSTDIR\${EXE}" "%1"'
  WriteRegStr HKLM "Software\Classes\Applications\${EXE}\SupportedTypes" ".pdf" ""
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
  DeleteRegKey HKLM "Software\Classes\${PROG_ID}"
  DeleteRegValue HKLM "Software\Classes\.pdf\OpenWithProgids" "${PROG_ID}"
  DeleteRegKey HKLM "Software\Classes\Applications\${EXE}"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
SectionEnd
