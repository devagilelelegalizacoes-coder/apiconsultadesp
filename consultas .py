import time
from selenium import webdriver
from selenium.webdriver.chrome.service import * #Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from anticaptchaofficial.imagecaptcha import *
from anticaptchaofficial.recaptchav2proxyless import *
import base64


class DebitoGRT:
    def __init__(self, renavam, cpf):
        self.renavam = renavam
        self.cpf = cpf
        self.url = 'https://www.ib7.bradesco.com.br/ibpfdetranrj/DebitoVeiculoRJGRTLoaderAction.do'
        self.navegador = webdriver.Chrome(service=Service(ChromeDriverManager().install()))


    def site(self):
               # Entrar no site detran

        self.navegador.get(self.url)
        time.sleep(3)

    def preenchercampos(self):
        #Preencher Renavam no Site
        self.navegador.find_element(By.CLASS_NAME,'campos_form').send_keys(self.renavam)

        time.sleep(1)
        #prencher cpf no Site
        self.navegador.find_element(By.ID,'txt-cpf-grd').send_keys(self.cpf[0:3])
        #
        # time.sleep(1)
        self.navegador.find_element(By.ID,'txt-cpf-grd-2').send_keys(self.cpf[3:6])
        #time.sleep(1)
        self.navegador.find_element(By.ID,'txt-cpf-grd-3').send_keys(self.cpf[6:9])
        #
        # time.sleep(1)
        self.navegador.find_element(By.ID,'txt-cpf-grd-4').send_keys(self.cpf[9:11])

        time.sleep(1)

    def captcha_imagem(self):

                    # Preencher Capcha imagem
        imagem = self.navegador.find_element(By.XPATH,'//*[@id="urlCaptcha"]').screenshot_as_base64
        #print(imagem)

        with open("imageToSave.jpeg","wb") as fh:
            fh.write(base64.urlsafe_b64decode(imagem))

        #pegar link Captha
        solver = imagecaptcha()
        solver.set_verbose(1)
        solver.set_key("e1baf93e04a768d02413ddfe321da197")

        #solver.set_soft_id(1074)

        captcha_text = solver.solve_and_return_solution("imageToSave.jpeg")

        if captcha_text != 0:
            retorno_captha = print ("captcha text: "+captcha_text)
            time.sleep(1)
            self.navegador.find_element(By.ID,'jcaptcha_response').send_keys(captcha_text)
            time.sleep(1)

        else:
            print ("task finished with error "+solver.error_code)


    def retorno_grt (self):

        self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/fieldset/span').click()

        time.sleep(1)

        abertos = self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/bt/div[2]/div/div/span[2]').text

        time.sleep(1)
        self.navegador.find_element(By.XPATH,'//*[@id="cdExercicio"]').click()

        nome_proprietario = self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/div[2]/div/span[1]/span').text
        cpf_proprietario = self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/div[2]/div/div[1]/span[4]/span').text
        placa_proprietario = self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/div[2]/div/div[1]/span[3]').text
        renavam_proprietario = self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/div[2]/div/div[1]/span[1]/span').text
        grt = self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/div[2]/div/div[2]/table/tbody/tr/td[4]').text
        exercicio = self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/div[2]/div/span[2]/span').text


        retorno = { "" : abertos, "exercicio": exercicio, "Nome": nome_proprietario, "CPF": cpf_proprietario, "Renavam": renavam_proprietario, "valor": grt}


class DebitoIPVA:
    def __init__(self,renavam,cpf):
        self.renavam = renavam
        self.cpf = cpf
        self.url = 'https://www.ib7.bradesco.com.br/ibpfdetranrj/debitoVeiculoRJLoader.do'
        self.navegador = webdriver.Chrome(service=Service(ChromeDriverManager().install()))


    def site(self):
               # Entrar no site detran

        self.navegador.get(self.url)
        time.sleep(3)

    def preenchercampos(self):
        #Preencher Renavam no Site
        self.navegador.find_element(By.CLASS_NAME,'campos_form').send_keys(self.renavam)

        time.sleep(1)
        #prencher cpf no Site
        self.navegador.find_element(By.ID,'txt-cpf-grd').send_keys(self.cpf[0:3])
        #
        # time.sleep(1)
        self.navegador.find_element(By.ID,'txt-cpf-grd-2').send_keys(self.cpf[3:6])
        #time.sleep(1)
        self.navegador.find_element(By.ID,'txt-cpf-grd-3').send_keys(self.cpf[6:9])
        #
        # time.sleep(1)
        self.navegador.find_element(By.ID,'txt-cpf-grd-4').send_keys(self.cpf[9:11])

        time.sleep(1)

    def captcha_imagem(self):

                    # Preencher Capcha imagem
        imagem = self.navegador.find_element(By.XPATH,'//*[@id="urlCaptcha"]').screenshot_as_base64
        #print(imagem)

        with open("imageToSave.jpeg","wb") as fh:
            fh.write(base64.urlsafe_b64decode(imagem))

        #pegar link Captha
        solver = imagecaptcha()
        solver.set_verbose(1)
        solver.set_key("e1baf93e04a768d02413ddfe321da197")

        #solver.set_soft_id(1074)

        captcha_text = solver.solve_and_return_solution("imageToSave.jpeg")

        if captcha_text != 0:
            retorno_captha = print ("captcha text: "+captcha_text)
            time.sleep(1)
            self.navegador.find_element(By.ID,'jcaptcha_response').send_keys(captcha_text)
            time.sleep(1)

        else:
            print ("task finished with error "+solver.error_code)

    def retornoipva(self):

        i = 0

        while i <= 3:
            self.navegador.find_elements(By.ID,"cdExercicio")[i].click()
            nome = self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/div[4]/div/div/span[5]/span').text
            cpf = self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/div[4]/div/div/span[6]/span').text
            ano = self.navegador.find_element(By.XPATH,'//*[@id="body-iframe"]/form/div[4]/div/div/span[4]/span').text
            cota = self.navegador.find_element(By.XPATH,'// *[ @ id = "body-iframe"] / form / div[5] / table / tbody / tr[1] / td[1]').text
            valor = self.navegador.find_element(By.XPATH,'// *[ @ id = "body-iframe"] / form / div[5] / table / tbody / tr[1] / td[3]').text
            self.navegador.back()
            retorno = {"Nome" : nome, "CPF" : cpf, "Exercício" : ano, "Cota" : cota, "Valor" : valor,}
            print(retorno)
            i = i+1



class cadastroDetran:

    def __init__(self,placa):
        self.placa = placa
        self.url = "https://www2.detran.rj.gov.br/portal/veiculos/consultaCadastro"
        self.navegador = webdriver.Chrome(service=Service(ChromeDriverManager().install()))


    def site(self):
        self.navegador.get(self.url)
        self.navegador.find_element(By.ID, 'placa').send_keys(self.placa)
        time.sleep(1)

    def captcha_imagem(self):

        chave_captcha = self.navegador.find_element(By.ID, 'divCaptcha').get_attribute('data-sitekey')

        solver = recaptchaV2Proxyless()
        solver.set_verbose(1)
        solver.set_key("e1baf93e04a768d02413ddfe321da197")
        solver.set_website_url("https://www2.detran.rj.gov.br")
        solver.set_website_key(chave_captcha)

        resposta = solver.solve_and_return_solution()

        if resposta != 0:
            print(resposta)
            # preencher o campo do token do captcha
            # g-recaptcha-response
            self.navegador.execute_script(f"document.getElementById('g-recaptcha-response').innerHTML = '{resposta}'")
            self.navegador.find_element(By.ID, 'btPesquisar').click()
        else:
            print(solver.err_string)

        time.sleep(2)

    def retorno(self):

        time.sleep(1)

        retorno_gravame = self.navegador.find_element(By.ID,'retorno').text[0:50]
        if retorno_gravame == 'EXISTE INFORMAÇÃO DE INCLUSÃO DE ALIENAÇÃO FIDUCIÁ':
            gravame = retorno_gravame
        else:
            gravame = ""
        ultimolicenciamento = self.navegador.find_element(By.ID,'crlv-licenciamento').text
        nomecrlv = self.navegador.find_element(By.ID,'crlv-nome').text
        placacrlv = self.navegador.find_element(By.ID,'crlv-placa').text
        especiecrlv = self.navegador.find_element(By.ID,'crlv-especie').text
        combustivelcrlv = self.navegador.find_element(By.ID,'crlv-combustivel').text
        marcacrlv = self.navegador.find_element(By.ID,'crlv-marca').text
        anofabricrlv = self.navegador.find_element(By.ID,'crlv-ano-fabricacao').text
        anomodcrlv = self.navegador.find_element(By.ID,'crlv-ano-modelo').text
        catcrlv = self.navegador.find_element(By.ID,'crlv-categoria').text
        corcrlv = self.navegador.find_element(By.ID,'crlv-cor').text
        obscrlv = self.navegador.find_element(By.ID,'crlv-observacoes').text
        localcrlv = self.navegador.find_element(By.ID,'crlv-local').text

        retorno = {"Gravame" : gravame, "Licenciamento" : ultimolicenciamento, "Nome" : nomecrlv, "Placa" : placacrlv, "Espaceie" : especiecrlv, "Combustivel" : combustivelcrlv, "Marca" : marcacrlv, "Fab" : anofabricrlv, "Mod" :anomodcrlv, "Categoria" : catcrlv, "Cor" : corcrlv, "Obs" : obscrlv, "Municipio" : localcrlv }
        print(retorno)



class MultaDetranRj:

    def __init__(self,renavam,cpf):
        self.renavam = renavam
        self.cpf = cpf
        self.url = "https://www2.detran.rj.gov.br/portal/multas/nadaConsta"
        self.navegador = webdriver.Chrome(service=Service(ChromeDriverManager().install()))


    def site(self):
        self.navegador.get(self.url)
        self.navegador.find_element(By.ID, 'MultasRenavam').send_keys(self.renavam)
        self.navegador.find_element(By.ID,'MultasCpfcnpj').send_keys(self.cpf)
        time.sleep(1)

    def captcha_imagem(self):

        chave_captcha = self.navegador.find_element(By.ID, 'divCaptcha').get_attribute('data-sitekey')

        solver = recaptchaV2Proxyless()
        solver.set_verbose(1)
        solver.set_key("e1baf93e04a768d02413ddfe321da197")
        solver.set_website_url("https://www2.detran.rj.gov.br")
        solver.set_website_key(chave_captcha)

        resposta = solver.solve_and_return_solution()

        if resposta != 0:
            print(resposta)
            # preencher o campo do token do captcha
            # g-recaptcha-response
            self.navegador.execute_script(f"document.getElementById('g-recaptcha-response').innerHTML = '{resposta}'")
            self.navegador.find_element(By.ID, 'btPesquisar').click()
        else:
            print(solver.err_string)

        time.sleep(2)

    def retornoMultas(self):

        tabela = self.navegador.find_element(By.CLASS_NAME,"tabelaDescricao").text

        print(tabela)






