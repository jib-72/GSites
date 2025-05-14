import sys, io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from datetime import datetime
from dotenv import load_dotenv


# 🔐 Configuración
load_dotenv()

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive = build('drive', 'v3', credentials=creds)

def is_debugging():
    return sys.gettrace() is not None

# 🔁 Recursivo: obtén todos los archivos y carpetas
def list_all_files(folder_id, parent_path=''):
    files = []
    page_token = None

    while True:
        response = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            spaces='drive',
            fields='nextPageToken, files(id, name, mimeType, modifiedTime)',
            pageToken=page_token
        ).execute()

        for f in response.get('files', []):
            full_path = f"{parent_path}/{f['name']}"
            if f['mimeType'] == 'application/vnd.google-apps.folder':
                files += list_all_files(f['id'], full_path)
            else:

                files.append({
                    'id': f['id'],
                    'name': f['name'],
                    'modifiedTime': f['modifiedTime'],
                    'path': full_path.strip('/'),
                    'mimeType': f['mimeType']
                })

        page_token = response.get('nextPageToken', None)
        if not page_token:
            break

    return files

# 🔍 Obtener todos los archivos en destino para comparación
def index_destination_files(dest_folder_id):
    dest_files = {}

    def walk(folder_id, current_path=''):
        page_token = None
        while True:
            results = drive.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                pageToken=page_token
            ).execute()

            for f in results.get('files', []):
                path = f"{current_path}/{f['name']}"
                if f['mimeType'] == 'application/vnd.google-apps.folder':
                    folder_ids[path] = f['id']
                    walk(f['id'], path)
                else:
                    dest_files[path.strip('/')] = {
                        'id': f['id'],
                        'modifiedTime': f['modifiedTime']
                    }

            page_token = results.get('nextPageToken')
            if not page_token:
                break

    folder_ids[''] = dest_folder_id
    walk(dest_folder_id)
    return dest_files

# 🗂️ Crear carpeta si no existe
def create_folder_if_needed(path):

    folders = path.split('/')
    parent_id = DEST_FOLDER_ID

    for folder in folders:
        current_path = '/'.join(folders[:folders.index(folder)+1])
        if current_path in folder_ids:
            parent_id = folder_ids[current_path]
            continue

        file_metadata = {
            'name': folder,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }

        folder = drive.files().create(body=file_metadata, fields='id').execute()
        folder_ids[current_path] = folder['id']
        parent_id = folder['id']

    return parent_id

# 📤 Descargar y subir archivo
def copy_file(file, parent_id):

    file_id = file['id']
    mime_type = file['mimeType']
    file_name = file['name']
    fh = io.BytesIO()

    mime_export_map = {

        'application/vnd.google-apps.document': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'),
        'application/vnd.google-apps.spreadsheet': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
        'application/vnd.google-apps.presentation': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', '.pptx'),
        'application/vnd.google-apps.drawing': ('image/png', '.png'),
    }

    if mime_type in mime_export_map:
        export_mime, extension = mime_export_map[mime_type]
        request = drive.files().export_media(fileId=file_id, mimeType=export_mime)
        file_name += extension
        upload_mime = export_mime
    elif mime_type.startswith('application/vnd.google-apps'):
        print(f"⚠️ Tipo de archivo Google no compatible para exportar: {mime_type}")
        return
    else:
        request = drive.files().get_media(fileId=file_id)
        upload_mime = mime_type  # Usa el mimeType original del archivo
        # opcional: asegúrate de que tenga una extensión si quieres renombrar
        # file_name += '.ext' (según tu lógica)

    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    fh.seek(0)
    #media = MediaIoBaseUpload(fh, mimetype=file['mimeType'])
    media = MediaIoBaseUpload(fh, mimetype=upload_mime) 

    metadata = {
        'name': file['name'],
        'parents': [parent_id]
    }

    drive.files().create(body=metadata, media_body=media, fields='id').execute()
    print(f"✅ Copiado: {file['path']}")

# 🧩 Estructuras auxiliares
folder_ids = {}
dest_index = index_destination_files(DEST_FOLDER_ID)

# 🚀 Proceso principal
source_files = list_all_files(SOURCE_FOLDER_ID)

for file in source_files:
    path = file['path']
    src_time = datetime.fromisoformat(file['modifiedTime'].replace('Z', '+00:00'))
    dest_file = dest_index.get(path)

    # Verifica si hay que copiar
    if dest_file:
        dest_time = datetime.fromisoformat(dest_file['modifiedTime'].replace('Z', '+00:00'))
        if src_time <= dest_time:
            print(f"⏩ Omitido (ya actualizado): {path}")
            continue

    folder_path = '/'.join(path.split('/')[:-1])
    parent_id = create_folder_if_needed(folder_path) if folder_path else DEST_FOLDER_ID
    copy_file(file, parent_id)
