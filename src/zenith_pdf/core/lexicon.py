"""Words people use for Zenith PDF's tools and commands, in English, Italian, French, Spanish, German and
Portuguese, each leading to the English words the app's own names are made of. The assistant's search
(core/finder.py) reads a question through it, so "ruota pagina", "pivoter la page" and "Seite drehen" all
find Rotate page. Written without accents: words are compared without them.
"""

from __future__ import annotations

# English term (as it appears in the app's names and keywords) -> what people may type instead.
SYNONYMS: dict[str, str] = {
    "highlight": """highlighter evidenzia evidenziare evidenziato evidenziatore evidenziazione evidenziazioni
        surligner surligneur surlignage surligne resaltar resaltado resaltador marcador destacar destaque
        marcatexto hervorheben hervorhebung textmarker leuchtstift markieren marca texto""",
    "underline": """sottolinea sottolineare sottolineato sottolineatura souligner souligne soulignement subrayar
        subrayado sublinhar sublinhado unterstreichen unterstrichen unterstreichung""",
    "strikethrough": """barra barrare barrato barrer barre biffer rayer tachar tachado riscar riscado rasurar
        durchstreichen durchgestrichen strike crossed""",
    "squiggle": "ondulata ondulato onda ondulee vague ondulado ondulada wellenlinie wellig wavy",
    "pen": """penna stilo matita pennarello stylo crayon plume boligrafo pluma lapiz caneta lapis stift
        kugelschreiber bleistift feder disegna disegnare disegno dessiner dessin dibujar dibujo desenhar desenho
        zeichnen zeichnung freehand inchiostro encre tinta tinte handschrift mano""",
    "eraser": """gomma gommare cancellino gomme effaceur borrador goma apagador borracha radiergummi radierer
        radieren rubber erase""",
    "text": """testo scritta scrivi scrivere texte ecrire texto escribir escrever schreiben textfeld""",
    "box": "casella riquadro cadre caja cuadro caixa kasten feld",
    "note": """nota note appunto commento commenti commentare postit commentaire commenter comentario comentar
        anotacion comentarios anotacao notiz kommentar kommentieren haftnotiz sticky""",
    "rectangle": "rettangolo quadrato riquadro rectangle carre rectangulo cuadrado retangulo quadrado rechteck quadrat",
    "ellipse": "ellisse cerchio ovale cercle elipse circulo oval kreis ellipse circle",
    "line": "linea riga retta ligne trait linea linha linie strich gerade",
    "arrow": "freccia freccetta fleche flecha seta pfeil",
    "polygon": "poligono polygone poligono polygon vieleck",
    "polyline": "spezzata polilinea polyligne polilinea polilinha polylinie",
    "cloud": "nuvola nuvoletta nuage nube nuvem wolke revisione revision",
    "callout": "fumetto richiamo nuvoletta bulle llamada globo balao textblase sprechblase legenda",
    "redaction": """oscura oscurare oscuramento censura censurare anonimizza anonimizzare caviardage caviarder
        noircir censurer masquer ocultar censurar anonimizar tarjar schwarzen schwarzung zensieren anonymisieren
        redact blackout""",
    "distance": """distanza lunghezza misura misurare righello regle mesurer mesure longueur regla medir medida
        distancia longitud regua comprimento lineal messen abstand lange entfernung measure ruler""",
    "area": "superficie aire area flache flacheninhalt",
    "perimeter": "perimetro perimetre umfang",
    "scale": "scala echelle escala massstab",
    "signature": "firma firmare signer signature firmar assinar assinatura unterschrift unterschreiben signieren sign",
    "image": """immagine immagini foto fotografia figura picture photo imagen imagenes imagem imagens bild bilder
        grafik""",
    "camera": "fotocamera webcam camera camara cameraphoto kamera",
    "link": """collegamento collegamenti ipertestuale lien liens hyperlien enlace enlaces vinculo hipervinculo ligacao
        hiperligacao verknupfung verweis hyperlink url""",
    "audio": """voce vocale registra registrare registrazione microfono suono enregistrer enregistrement voix
        micro son grabar grabacion voz microfono sonido gravar gravacao microfone som aufnehmen aufnahme stimme
        mikrofon ton sprachnotiz sound voice record recording microphone""",
    "equation": "equazione formula formule matematica equation mathematique ecuacion formula matematica equacao gleichung formel mathe latex math",
    "snapshot": "istantanea cattura screenshot ritaglio capture instantane captura recorte schnappschuss bildschirmfoto ausschnitt",
    "select": "seleziona selezionare selezione selectionner selection seleccionar seleccion selecionar selecao auswahlen auswahl",
    "lasso": "lazo laco",
    "pan": "mano sposta spostare trascina main deplacer mover desplazar mao verschieben hand move drag",
    "laser": "puntatore pointeur puntero ponteiro zeiger presentazione presentation presentacion apresentacao prasentation pointer",
    "tape": "nastro scotch ruban cinta fita klebeband",
    "study": "studio studiare quiz ripasso etude etudier estudio estudiar estudo estudar lernen ripassare",
    "bookmark": "segnalibro segnalibri preferito marquepage signet marcador marcadores favorito lesezeichen",
    "cleaner": "pulisci pulire nettoyer limpiar limpar bereinigen sauber",
    "rotate": "ruota ruotare girare gira rotazione raddrizza tourner pivoter rotation girar rotar rotacion rodar rotacao drehen rotieren turn",
    "page": "pagina pagine foglio page pages pagina paginas seite seiten blatt",
    "pages": "pagine paginas seiten",
    "delete": """elimina eliminare cancella cancellare rimuovi rimuovere togli togliere supprimer effacer enlever
        retirer borrar eliminar quitar apagar excluir remover loschen entfernen remove""",
    "add": """aggiungi aggiungere inserisci inserire metti mettere ajouter inserer anadir agregar insertar adicionar
        inserir colocar hinzufugen einfugen insert""",
    "new": "nuovo nuova crea creare nouveau nouvelle creer nuevo nueva crear novo nova criar neu neue erstellen create",
    "blank": "bianca vuota bianco vierge vide blanco vacia branca vazia leer leere empty",
    "save": "salva salvare enregistrer sauvegarder guardar salvar gravar speichern sichern",
    "open": "apri aprire ouvrir abrir offnen",
    "print": "stampa stampare imprimer imprimir drucken",
    "close": "chiudi chiudere fermer cerrar fechar schliessen",
    "tab": "scheda schede onglet pestana aba registerkarte",
    "search": """cerca cercare trova trovare ricerca chercher rechercher trouver recherche buscar encontrar busqueda
        procurar pesquisar pesquisa suchen finden suche find""",
    "zoom": "ingrandisci ingrandire rimpicciolisci ingrandimento agrandir zoomer ampliar acercar aumentar vergrossern verkleinern",
    "fit": "adatta adattare ajuster adapter ajustar encaixar anpassen",
    "width": "larghezza largeur ancho largura breite",
    "compress": """comprimi comprimere compressione riduci ridurre dimensione peso leggero alleggerire compresser
        compression reduire taille comprimir compresion reducir tamano reduzir tamanho komprimieren komprimierung
        verkleinern grosse smaller size""",
    "password": """parola chiave proteggi proteggere protezione cifra crittografa crittografia passe proteger
        chiffrer contrasena cifrar senha criptografar passwort kennwort schutzen verschlusseln encrypt protect""",
    "permissions": "permessi autorisations permisos permissoes berechtigungen rechte",
    "split": "dividi dividere separa separare diviser separer dividir separar teilen trennen aufteilen",
    "merge": """unisci unire combina combinare fondere fusionner combiner assembler unir combinar juntar
        zusammenfuhren kombinieren combine join""",
    "crop": "ritaglia ritagliare margini rogner recadrer marges recortar cortar margenes zuschneiden beschneiden rander",
    "margin": "margine marge margen margem rand",
    "theme": "tema temi theme themes thema aspetto apparence apariencia aparencia aussehen appearance",
    "dark": "scuro scura buio sombre nuit oscuro oscura escuro escura dunkel",
    "light": "chiaro chiara clair claire claro clara hell",
    "mode": "modalita modo modus",
    "settings": """impostazioni preferenze opzioni configurazione parametres preferences options reglages
        configuracion ajustes preferencias configuracoes definicoes einstellungen optionen""",
    "shortcut": """scorciatoia scorciatoie tasti tastiera combinazione raccourci raccourcis clavier touches atajo atajos
        teclado teclas atalho atalhos tastenkurzel tastatur tastenkombination keys keyboard hotkey""",
    "guide": """guida aiuto manuale istruzioni aide manuel ayuda guia manual instrucciones ajuda instrucoes
        handbuch hilfe anleitung tutorial help learn""",
    "reading": """lettura leggere lecture lire lectura leer leitura ler lesen lesemodus concentrazione focus
        distrazioni""",
    "full": "intero pieno plein completa cheia voll",
    "screen": "schermo ecran pantalla tela bildschirm vollbild fullscreen",
    "undo": "annulla annullare indietro defaire annuler deshacer desfazer ruckgangig",
    "redo": "ripeti ripristina retablir refaire rehacer refazer wiederholen",
    "copy": "copia copiare copier copiar kopieren",
    "paste": "incolla incollare coller pegar colar einfugen",
    "cut": "taglia tagliare couper cortar recortar ausschneiden",
    "spelling": """ortografia ortografico correttore correggi correggere correzione errori errore refusi refuso
        orthographe correcteur corriger correction fautes faute ortografia corrector corregir correccion errores
        corretor corrigir correcao erros rechtschreibung korrektur korrigieren fehler spell spellcheck typo typos
        mistakes correct""",
    "punctuation": "punteggiatura spazi ponctuation espaces puntuacion espacios pontuacao espacos zeichensetzung leerzeichen",
    "summary": """riassunto riassumi riassumere sintesi sintetizza sintetizzare schema resume resumer synthese
        resumen resumir sintesis resumo sintese zusammenfassung zusammenfassen kurzfassung summarise summarize key
        points""",
    "notes": "appunti fiche fiches apuntes apontamentos notizen lernzettel",
    "export": "esporta esportare exporter exportar exportieren",
    "ocr": """riconoscimento scansione scansionato scannerizzato reconnaissance numerisation scanne reconocimiento
        escaneo escaneado reconhecimento digitalizacao digitalizado texterkennung scan scanned recognise""",
    "watermark": "filigrana filigrane marca agua dagua wasserzeichen",
    "numbers": "numeri numerazione numera numeroter numeros numerar numeracao nummerieren seitenzahlen nummern",
    "translate": "traduci tradurre traduzione traduire traduction traducir traduccion traduzir traducao ubersetzen ubersetzung",
    "explain": "spiega spiegare spiegazione expliquer explication explicar explicacion explicacao erklaren erklarung",
    "rewrite": "riscrivi riscrivere riformula riformulare parafrasi reecrire reformuler reescribir reformular reescrever umschreiben umformulieren paraphrase",
    "two": "due doppia deux double dos duas doppelt zwei",
    "book": "libro livre livro buch",
    "night": "notte notturna nuit nocturne noche nocturno noite noturno nacht",
    "sepia": "seppia",
    "invert": "invertito invertire inverti inverser invertir inverter invertieren negative",
    "snapping": "magnete magnetico allinea allineare allineamento aimanter aimant aligner magnetismo alinear alinhar ausrichten einrasten magnet align",
    "lock": "blocca bloccare lucchetto fissa verrouiller bloquer bloquear trancar sperren fixieren",
    "colour": "colore colori couleur couleurs color colores cor cores farbe farben color",
    "thickness": "spessore grandezza epaisseur grosor espesor espessura starke dicke size",
    "opacity": "opacita trasparenza opacite transparence opacidad transparencia opacidade transparencia deckkraft transparenz",
    "author": "autore nome auteur nom autor nombre nome verfasser name",
    "autosave": "automatico automatica automatique automatico automatisch",
    "glass": """vetro trasparente trasparenza sfocatura sfocato verre flou transparence vidrio desenfoque transparencia
        vidro desfoque glas unscharf transparenz transparent transparency acrylic blur""",
    "tools": "strumenti strumento attrezzi outils outil herramientas herramienta ferramentas ferramenta werkzeuge werkzeug",
    "attachments": "allegati allegato allega allegare pieces jointes joindre adjuntos adjuntar anexos anexo anexar anhange anhang anhangen",
    "properties": "proprieta informazioni metadati proprietes informations proprietes propiedades informacion propriedades informacoes eigenschaften metadaten info",
    "flatten": "appiattisci appiattire aplatir aplanar achatar nivelar reduzieren",
    "go": "vai andare vado aller va ir ve vamos gehe gehen springe springen goto jump salta saltare",
    "back": "indietro torna tornare tornare retour revenir precedente atras volver regresar voltar zuruck",
    "forward": "avanti suivant adelante avancar vorwarts",
    "library": "libreria raccolta timbri timbro bibliotheque tampon tampons biblioteca sello sellos carimbo carimbos bibliothek stempel stamp",
    "templates": """modelli modello carta righe righi quadretti puntini quaderno modeles modele papier lignes
        quadrille plantillas plantilla papel lineas cuadricula modelos modelo papel linhas quadriculado vorlagen
        vorlage papier linien kariert punktiert lined grid dotted""",
    "tips": "consigli suggerimenti trucchi conseils astuces consejos trucos dicas truques tipps tricks hints",
    "language": "lingua lingue langue idioma lingua sprache",
    "accent": "accento accentuation",
    "thumbnails": "miniature anteprime vignettes miniaturas miniaturen",
    "panel": "pannello barra laterale panneau lateral panel lateral painel seitenleiste",
    "toolbar": "barra strumenti barre outils barra herramientas barra ferramentas werkzeugleiste symbolleiste",
    "manager": "gestore gestione organizza riordina gestionnaire organiser gestor organizar gerenciador organizar verwaltung organisieren reorder",
    "duplicate": "duplica duplicare dupliquer duplicar duplizieren",
    "extract": "estrai estrarre extraire extraer extrair extrahieren",
    "sanitize": "pulisci sanifica nettoyer limpiar higienizar bereinigen",
    "repair": "ripara riparare danneggiato corrotto reparer endommage reparar danado danificado reparieren beschadigt",
    "pdf": "documento documenti document documents documento documentos dokument dokumente file",
    "file": "file fichier archivo arquivo datei",
    "images": "immagini images imagenes imagens bilder pictures photos",
    "builder": "costruttore assembla assemblare",
    "word": "parola mot palabra palavra wort",
    "window": "finestra finestre fenetre fenetres ventana ventanas janela janelas fenster",
    "side": "affiancati affiancato fianco cote lado lados nebeneinander",
    "comment": "commento commentaire comentario kommentar",
    "compare": """confronta confrontare confronto paragona paragonare differenze differenza comparer comparaison
        differences comparar comparacion diferencias comparacao diferencas vergleichen vergleich unterschiede""",
    "version": "versione versioni revisione versions versiones versao versoes fassung",
    "update": """aggiorna aggiornare aggiornamento aggiornamenti aggiornato mettre jour actualiser actualizar
        actualizacion actualizaciones atualizar atualizacao atualizacoes aktualisieren aktualisierung
        aktualisierungen upgrade""",
    "aloud": """alta voce leggimi voix haute voz alta vorlesen laut sprachausgabe""",
    "speak": """parla parlare pronuncia ascolta ascoltare parler ecouter hablar escuchar falar ouvir sprechen
        anhoren listen""",
    "flashcards": """flashcard schede carte memoria cartes memoire tarjetas tarjeta cartoes cartao karteikarten
        lernkarten karteikarte anki""",
    "every": "ogni tutti tutte qualsiasi tous toutes chaque todos todas cada alle jede jeder",
    "inside": "dentro interno interni dans interieur innerhalb",
    "default": """predefinito predefinita predefiniti impostare principale defaut predetermine predeterminado
        predeterminada principal padrao standard standardprogramm standardmassig""",
    "reader": "lettore visualizzatore lecteur visionneuse lector visor leitor visualizador leser betrachter viewer",
    "forms": """modulo moduli compila compilare compilazione campi campo formulaire formulaires remplir champ champs
        formulario formularios rellenar cumplimentar campos preencher formular formulare ausfullen felder""",
}

# Words that only carry the question: "how do I", "come faccio a", "comment", "wie kann ich" and the like.
FILLER = frozenset("""how come comment como wie can could may posso puoi puo potrei peux puis pourrais puedo puede
    podria pode podia kann konnte i io je yo eu ich want wanna voglio vorrei vuoi veux voudrais quiero quisiera
    quero queria mochte will willst do does did fare faccio fai faire fais hacer hago fazer faco machen mache where
    dove ou donde onde wo what cosa quoi que qual welche welcher which there ce ci hay tem gibt please favore plait
    favor bitte my mio mia miei mie mon ma mes mi mis meu minha mein meine use usare usa uso utiliser utilise usar
    utilizar benutzen verwenden way modo maniera facon manera forma weise get ottenere obtenir obtener obter bekommen
    possible possibile posible possivel moglich need serve bisogno besoin necesito preciso brauche to a""".split())
