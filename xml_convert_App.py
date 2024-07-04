from flask import Flask, request, redirect, url_for, render_template, flash, send_file, make_response
import os
import xml.etree.ElementTree as ET
import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'output'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

# Google Sheets API credentials
credentials_path = "archive/Source/service_acc_cred.json"
spreadsheet_id = '1Nw8Kda2lXvXiuHB7-qjZZqjXc_bppETccZJvnXKJM0c'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

def create_service():
    try:
        credentials = service_account.Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        service = build('sheets', 'v4', credentials=credentials)
        return service
    except Exception as e:
        print(f"Failed to create Google Sheets service: {e}")
        raise

def get_last_row(sheet_values):
    return len(sheet_values) + 1 if sheet_values else 1

def upload_to_google_sheets(data, spreadsheet_id, sheet_name):
    try:
        service = create_service()

        sheet_values = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f'{sheet_name}!A:Z'
        ).execute().get('values', [])

        last_row = get_last_row(sheet_values)
        values = data.values.tolist()

        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f'{sheet_name}!A{last_row}',
            valueInputOption='RAW',
            body={'values': values}
        ).execute()

        print(f"Data has been appended to Google Sheet '{sheet_name}'.")
    except HttpError as error:
        print(f"An HTTP error occurred: {error}")
    except Exception as e:
        print(f"An error occurred: {e}")

def extract_invoice_data(xml_file):
    try:
        tree = ET.parse(xml_file)
        root = tree.getroot()

        header = root.find('.//FatturaElettronicaHeader')
        cedente_prestatore = header.find('CedentePrestatore')
        fornitore = cedente_prestatore.find('DatiAnagrafici/Anagrafica/Denominazione').text
        partita_iva = cedente_prestatore.find('DatiAnagrafici/IdFiscaleIVA/IdCodice').text

        body = root.find('.//FatturaElettronicaBody')
        general_data = body.find('DatiGenerali/DatiGeneraliDocumento')
        lines = body.findall('DatiBeniServizi/DettaglioLinee')
        dati_ddt = body.findall('DatiGenerali/DatiDDT')
        numDati_ddt = len(dati_ddt)

        data_list = []
        ddt_Line1=''
        numero_ddt=0
        data_ddt=0
        ddt_Line=''
        
        for eachDDT in dati_ddt:
            numero_ddt = eachDDT.find('NumeroDDT').text
            data_ddt = eachDDT.find('DataDDT').text
           
            riferimento_numero_linea_elements = eachDDT.findall('RiferimentoNumeroLinea')          

            for riferimento_numero_linea in riferimento_numero_linea_elements:
                data_list.append({
                    'RiferimentoNumeroLinea': riferimento_numero_linea.text,
                    'DataDDT': data_ddt,
                    'NumeroDDT': numero_ddt
                })

            
        def get_data_by_riferimento_numero_linea_from_list(riferimento_numero):
            for item in data_list:
                if item['RiferimentoNumeroLinea'] == riferimento_numero:
                    return item['NumeroDDT'], item['DataDDT']
            return None, None

        data_doc = general_data.find('Data').text
        numero_doc = general_data.find('Numero').text

        line_items = []
        for line in lines:
            numero_linea = line.find('NumeroLinea').text
            ddt_info = get_data_by_riferimento_numero_linea_from_list(numero_linea)           
           
            if ddt_info is not None and numDati_ddt == 1:
                ddt_Line=(str(numero_ddt)) + " DEL " +str(data_ddt) 
            else:
                ddt_Line = (str(ddt_info[0])) + " DEL " +str(ddt_info[1])                 

            percentuale_values = []
            sconti_elements = line.findall('.//ScontoMaggiorazione')
            for sconto in sconti_elements:
                percentuale = sconto.find('Percentuale').text
                percentuale_values.append(percentuale)
            Sconto = ' + '.join(percentuale_values)

            line_data = {
                'Data': data_doc,
                'N Doc': numero_doc,
                'Codice': line.find('CodiceArticolo/CodiceValore').text if line.find('CodiceArticolo/CodiceValore') is not None  else line.find('AltriDatiGestionali/RiferimentoTesto').text if line.find('AltriDatiGestionali/RiferimentoTesto') is not None else '',
                'Descrizione': line.find('Descrizione').text,
                'Importo Unitario': line.find('PrezzoUnitario').text,
                'QuantitÀ': line.find('Quantita').text if line.find('Quantita') is not None else '',
                'Sconto': Sconto if Sconto != '' else 0 ,
                'Totale': line.find('PrezzoTotale').text,
                'UnitÀ Di Misura': line.find('UnitaMisura').text if line.find('UnitaMisura') is not None else '',
                'Fornitore': fornitore,
                'Partita IVA': partita_iva,
                'commessa':None,
                'Sottocommessa': None,
                'Descrizione interna':  ddt_Line 
            }

            # Remove 'Descrizione interna' if its value is "None DEL None"
            if line_data['Descrizione interna'] == "None DEL None":
                del line_data['Descrizione interna']


            if line_data['Codice']:
                line_items.append(line_data)
        
            #line_items.append(line_data)

        return pd.DataFrame(line_items)
    except ET.ParseError as e:
        print(f"Failed to parse XML file: {e}")
        raise
    except Exception as e:
        print(f"An error occurred while extracting data: {e}")
        raise

@app.route('/')
def upload_form():
    return render_template('upload.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('No file part')
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect(request.url)
    if file:
        xml_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(xml_path)
        outputFile = file.filename.replace('.xml', '.xlsx')
        try:
            df_line_items = extract_invoice_data(xml_path)
            
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], outputFile)
            df_line_items.to_excel(output_path, sheet_name='InvoiceData', index=False)
            print(f"Data has been extracted and saved to '{output_path}'")

            upload_to_google_sheets(df_line_items, spreadsheet_id, 'Sheet1')

            # Set output_path in a cookie
            resp = make_response(redirect(url_for('success')))
            resp.set_cookie('output_path', output_path)
            return resp
        except Exception as e:
            flash(f"An error occurred: {e}")
            return redirect(request.url)

@app.route('/success')
def success():
    # Retrieve output_path from the cookie
    output_path = request.cookies.get('output_path')
    print(f"Output path received in success route: {output_path}")  # Debug print
    return render_template('success.html', data=None, output_path=output_path)

@app.route('/download_excel')
def download_excel():
    try:
        # Retrieve output_path from the cookie
        output_path = request.cookies.get('output_path')
        print(f"Output path received in download_excel route: {output_path}")  # Debug print
        if not output_path:
            flash("No file to download")
            return redirect(url_for('success'))
        return send_file(output_path, as_attachment=True)
    except Exception as e:
        flash(f"An error occurred while downloading the Excel file: {e}")
        return redirect(url_for('success'))

if __name__ == '__main__':
    app.run(debug=True)
