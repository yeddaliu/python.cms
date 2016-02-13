# -*- coding: utf-8 -*-
from __future__ import division
import sys, os, traceback, glob
import time, datetime
from time import  strftime
from datetime import date
from datetime import timedelta
import ConfigParser
import pymssql
from xml.dom.minidom import Document
import subprocess, re, string
import socket
import smtplib
import json

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
from email import encoders
import mimetypes
# set UTF8 as default encoding because test DB server (develop env) encode issue
reload(sys)
sys.setdefaultencoding("utf8")

class CmsSync():
    def __init__ (self):
        self.cmd_fetch_list = ['fetch_data']
        self.debug = False
        self.is_deploy = False
        self.conn = None
        self.is_error_terminated = False

    def run(self, sys_argvs):
        cmd2do = []
        for argv in sys_argvs:
            # enable debug mode ?
            if '--debug' == argv:
                self.debug = True
            elif 'deploy' == argv:
                self.is_deploy = True
            # do specific command ?
            elif argv in self.cmd_fetch_list:
                cmd2do.append(argv)
            else:
                cmd2do = ['invalid_cmd']
                break
       
        if 'invalid_cmd' in cmd2do:
            getattr(self, 'invalid_cmd')()
            return

        # start load cofig when command is right
        self.config = CmsConfig
        self.logger = LogWriter(self.config)
        # log dir exists ?
        if not os.path.exists(self.config.get_log_path()):
            os.makedirs(self.config.get_log_path())
        # data backup dir exists ?
        if not os.path.exists(self.config.get_data_backup_path()):
            os.makedirs(self.config.get_data_backup_path())

        # if none of fetch/deploy cmds, enable deploy. It means enable every cmds.
        if not self.is_deploy and not(len(cmd2do)):
            self.is_deploy = True
            cmd2do = self.cmd_fetch_list
            
        welcom_msg = '''
    
##################################
##                              ##
##      CMS Sync                ##
##                              ##
##################################

'''
        self.logger.print_msg(welcom_msg);
    
        # do specific or all commands as default behavior            
        if len(cmd2do):
            for cmd in cmd2do:
                getattr(self, cmd)()
            self.__close_connection()
        # final process if any of fetch action exists
        if len(cmd2do) and cmd2do:    
            self.sync_assets()
            self.fetch_final()    
        # deploy as command    
        if self.is_deploy:
            self.deploy()
    
        self.logger.print_msg(self.logger.sep_line('PROG Terminated'))
        self.logger.print_msg('\r\n' * 3)
            
        if self.config.is_send_notify_enabled():
            mailer = SendNotify(self.config)
            mailer.send_sync_notify(self.is_error_terminated)
            mailer.close()
            if self.debug and self.config.get_smtp_config()['mail_to']:
                print "Send Notify Mail to [%s]" % (self.config.get_smtp_config()['mail_to'])

        
    def __get_connection(self):        
        if self.conn is not None:
           return self.conn 

        os.environ["TDSVER"] = "8.0" # Correct TDS Version should be set before DB connection
        try:
            db_cfg = self.config.get_db_config()
            self.conn = pymssql.connect(
                  host = db_cfg['host'] + ':' + db_cfg['port']
                , user = db_cfg['user']
                , password = db_cfg['password']
                , database = db_cfg['database']
                , timeout = 10 # query timeout
                , login_timeout = 2 # connection & login timeout
                , charset = "utf8"
                , as_dict = True
            )
        except:
            self.logger.print_msg('     ' + self.logger.color_str('warning', 'Connection error!\n\n'))
            tb = traceback.format_exception(sys.exc_info()[0],sys.exc_info()[1],sys.exc_info()[2])
            self.logger.print_msg('     ' + self.logger.color_str('warning', ''.join(tb)))
            self.conn = None

        return self.conn

    def __close_connection(self):
        if self.conn is None:
           return
        self.conn.close()

    def invalid_cmd (self):
        # print error message for invalid command
        self.logger.print_msg(self.logger.color_str('warning', 'Syntex: python main.py [--debug [fetch_data | deploy]]'))

    def fetch_data (self):
        if self.is_error_terminated:
            return

        self.logger.print_msg(self.logger.sep_line('FETCH DATA'))
        conn = self.__get_connection()
        if conn is None:
            self.is_error_terminated = True
            return

        data_path = self.config.get_data_path()

        # initialize CMS data dirs
        self.logger.print_msg(' * [%s]initialize data dir...' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))))
        self.__fetch_dir_handler(['product'], True)

        # fetch data from CMS
        # convert cms data to xml files
        self.logger.print_msg(' * [%s]Generate xml files ...' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))))

        ### [ product data ] ########################################################
        xml_r_path = 'product'
        features_dict = {}
        cur = conn.cursor()
        cur.execute("select * from feature, feature_tips where feature.fid = feature_tips.fid")
        for d in cur.fetchall_asdict():
            fid = str(d['fid'])
            code = str(d['lang']).upper()
            if fid not in features_dict:
                features_dict[fid] = {
                      'imgSrc': d['imgSrc']
                    , 'i18n': {}
                }
            features_dict[fid]['i18n'][code] = { 'tip': d['tip'] }

        for code in self.config.get_all_lang_codes():
            xml_name = 'features_' + code
            self.logger.print_msg('   - %s' % (os.path.join(xml_r_path, xml_name + '.xml')))
            doc = Document()
            _list = doc.createElement("features")
            for fid, f in features_dict.items():
                _f = doc.createElement("feature")
                # id
                _f.setAttribute('id', fid)
                # tip(this language is not found => use 'EN')
                index = 'tip'
                value = f.get('i18n', None) and (f['i18n'].get(code, None) and f['i18n'][code].get(index, '')) or (f['i18n'].get('EN', '') and f['i18n']['EN'].get(index, ''))
                _e = doc.createElement(index)
                _e.appendChild(doc.createTextNode(u'%s'%value))
                _f.appendChild(_e)
                # imgSrc
                index = 'imgSrc'
                _e = doc.createElement(index)
                _e.appendChild(doc.createTextNode(str(f.get(index, '')) ))
                _f.appendChild(_e)

                _list.appendChild(_f)

            doc.appendChild(_list)
            self.__write2xml(xml_r_path, xml_name, doc)
            
        features_dict.clear()

    def sync_assets(self):
        if self.is_error_terminated:
            return

        self.logger.print_msg(self.logger.sep_line('SYNC ASSETS'))
        data_path = self.config.get_data_path()
        assets_path = os.path.join(data_path, 'assets')

        self.logger.print_msg(' * [%s]initialize data dir...' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))))
        # consider not clear all assets whenever fetch data later. In this way, we will save wget time but wast storage size. 2013-04-08 by Yedda
        self.__fetch_dir_handler([
            "assets"
        ], True)

        # sync assets from CMS (by FTP ?)
        ftp_cfg = self.config.get_ftp_config()
        self.logger.print_msg(' * [%s]Sync assets(%s) <== [CMS]' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), assets_path))
        cmd = [
            'wget -r -nH -N -P "%s" --user="%s" --password="%s" "ftp://%s:%s"' % (
                  assets_path
                , ftp_cfg['user']
                , ftp_cfg['password']
                , ftp_cfg['host']
                , ftp_cfg['port']
            )
        ]
        self.__cmd(cmd, lambda str: self.logger.print_msg(str))

    def fetch_final(self):
        if self.is_error_terminated:
            return

        self.logger.print_msg(self.logger.sep_line('FETCH FINAL'))

        # create version tag file
        self.logger.print_msg(' * [%s]Create version tag(%s) of generated data' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), self.config.get_session_id()))
        cmd = 'echo "%s" > "%s"' % (self.config.get_session_id(), os.path.join(self.config.get_data_path(), 'version.txt'))
        self.__cmd(cmd)

        # backup generated data
        self.logger.print_msg(' * [%s]Backup generated data' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))))
        cmd = 'cp -R "%s" "%s"' % (self.config.get_data_path(), os.path.join(self.config.get_data_backup_path(), 'fetch-' + self.config.get_session_id()))
        self.__cmd(cmd)

    def deploy (self):
        if self.is_error_terminated:
            return

        self.logger.print_msg(self.logger.sep_line('DEPLOY TO SITES'))

        data_path = self.config.get_data_path()
        data_dirname = self.config.get_data_dirname()

        # sync all files to each Product site (rsync by ssh)
        sites = self.config.get_sync_sites()
        for site, host_list in sites:
            self.logger.print_msg(self.logger.color_str('warning', '>>>>> Sync data [' + site + '] <<<<<') )

            hp_list = []
            for host_profile in host_list.split(','):
                h = host_profile.split(':')
                host = h[0].strip()
                web_path = h[1].strip()
                # query ip list from DNS ? (format: username@[hostname])
                result = re.compile(r"([^@]+)@\[([^@]+)\]").search(host)
                if result:
                    username = result.group(1)
                    for ip in self.__get_ip_by_name(result.group(2)):
                        hp_list.append({
                              "user": username
                            , "host": username + '@' + ip
                            , "web_path": web_path
                            , "connected": False                            
                        })
                else:
                    h2 = host.split(':')
                    username = h2[0].strip()
                    hp_list.append({
                          "user": username or None
                        , "host": host
                        , "web_path": web_path
                        , "connected": False
                    })

            # sync data in each server
            for h, hp in enumerate(hp_list):
                host = hp['host']
                web_path = hp['web_path']
                rcmd_go2web = 'cd %s' % web_path
                sync_data_dirname = data_dirname + '-' + self.config.get_session_id()

                self.logger.print_msg('[%s] %s' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), self.logger.color_str('ok', '[ ' + string.join((host, web_path), ':') + ' ]')))

                # server responds ?
                rcmd = 'ls -l "%s"' % web_path # list web document files
                output = self.__remote_cmd(host, (rcmd))
                result = output or None
                if result:
                    hp['connected'] = True
                else:
                    # if keep happened, add logger server to route maybe can fix it (need OP support)
                    self.logger.print_msg(self.logger.color_str('warning', '[WARN]!!!%s not respond!!!'%(host)))
                    self.is_error_terminated = True
                    continue

                # data dir link exist ?
                curr_data_dir_path = None
                rcmd = 'readlink "%s"' % data_dirname # Does CMSData has symbolic link
                output = self.__remote_cmd(host, (rcmd_go2web, rcmd))
                if (not output):
                    # fix CMSData dir without link will make link symbolic link fail, delete it.
                    # 1. forgot rebuild symbolic link after delpoy 2. exception occurred
                    self.logger.print_msg('%s does not has symbolic link, remove it.' % data_dirname)
                    rcmd = 'rm -rf "%s"' % data_dirname
                    output = self.__remote_cmd(host, (rcmd_go2web, rcmd))
                else:
                    hp_list[h]['curr_data_dir_path'] = curr_data_dir_path = output
                    rcmd = 'test -d "%s" && echo 1' % curr_data_dir_path # check data dir
                    # current data dir exists ?
                    output = self.__remote_cmd(host, (rcmd_go2web, rcmd))
                    if output and (output.strip() == '1'):
                        self.logger.print_msg('Data dir exists(%s).' % curr_data_dir_path)
                    else:
                        hp_list[h]['curr_data_dir_path'] = curr_data_dir_path = None
                        self.logger.print_msg('Data dir does not exists(%s).' % curr_data_dir_path)

                # copy current data dir to sync
                if curr_data_dir_path:
                    rcmd = 'cp -R "%s" "%s"' % (curr_data_dir_path, sync_data_dirname)
                    output = self.__remote_cmd(host, (rcmd_go2web, rcmd))
                    self.logger.print_msg('Copy "%s" to "%s"' % (curr_data_dir_path, sync_data_dirname))
                else:
                    self.logger.print_msg('Can not copy "%s" to "%s", first time sync?' % (curr_data_dir_path, sync_data_dirname))

                # do sync! (local data -> remote temp dir)
                self.logger.print_msg('Sync local data(%s) to "%s:%s"' % (data_path, host, os.path.join(web_path, sync_data_dirname)))
                cmd = ['rsync --delete -ravz --checksum --ignore-times --progress %s/ %s:%s/%s' % (data_path, host, web_path, sync_data_dirname)]
                def onOutputChange (s):
                    # progress or complete?
                    result_progress = re.compile(r"to-check=(\d+)/(\d+)").search(s)
                    result_complete = re.compile(r"^total size").search(s)
                    if result_progress:
                        num_rest = int(result_progress.group(1))
                        num_total = int(result_progress.group(2))
                        progress = int(float((num_total - num_rest) / num_total) * 100)
                        (0 == progress % 10) and self.logger.print_msg(str(progress) + '%');
                    elif result_complete:
                        self.logger.print_msg('Sync complete.');

                self.__cmd(cmd, onOutputChange)

            # publish synced data in each server:MUST execute after all data synced
            self.logger.print_msg('\r\n')
            self.logger.print_msg(self.logger.color_str('warning', '>>>>> Publish synced data [' + site + '] <<<<<'))                        
            for h, hp in enumerate(hp_list):
                host = hp['host']
                web_path = hp['web_path']
                user = hp['user']
                rcmd_go2web = 'cd %s' % web_path
                curr_data_dir_path = ('curr_data_dir_path' in hp_list[h]) and hp_list[h]['curr_data_dir_path'] or None
                
                self.logger.print_msg('[%s] %s' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), self.logger.color_str('ok', '[ ' + string.join((host, web_path), ':') + ' ]')) )                

                if hp['connected'] == False:
                    self.logger.print_msg(self.logger.color_str('warning', '[WARN]host data have not been synced'))                
                    self.is_error_terminated = True
                    continue

                # change synced data permission & udpate data dir link to synced dir
                self.logger.print_msg('change data dir permission to %s:0755'%user)
                rcmd1 = 'chmod -R 0755 "%s"' % (sync_data_dirname)
                if user is None:
                    output = self.__remote_cmd(host, (rcmd_go2web, rcmd1))
                else:
                    rcmd2 = 'chown -R %s:%s "%s"' % (user, user, sync_data_dirname)
                    output = self.__remote_cmd(host, (rcmd_go2web, rcmd1, rcmd2))
                result = output or None
                if result is not None:
                    self.logger.print_msg(self.logger.color_str('warning', '[WARN]change data dir permission fail. Msg: %s'%result.trip()))
                    self.is_error_terminated = True
 
                # udpate data dir link to synced dir
                self.logger.print_msg('update data dir link to "%s"' % (sync_data_dirname) )
                rcmd = 'ln -sfn "%s" "%s"' % (sync_data_dirname, data_dirname)
                output = self.__remote_cmd(host, (rcmd_go2web, rcmd))
                result = output or None
                if result is not None:
                    self.logger.print_msg(self.logger.color_str('warning', '[WARN]update data dir link fail. Msg: %s'%result.trip()))
                    self.is_error_terminated = True

                # check link dir is exixt after bulid symbolic link?
                rcmd = 'test -d "%s" && echo 1' % sync_data_dirname
                output = self.__remote_cmd(host, (rcmd_go2web, rcmd))
                if output and (output.strip() == '1'):
                    self.logger.print_msg('Sync data dir exists(%s).' % sync_data_dirname)
                else:
                    self.logger.print_msg(self.logger.color_str('warning', '[WARN] sync data (%s) dir not exist.' % sync_data_dirname))
                    self.is_error_terminated = True

                # move current data dir to "[data dir name].bk"
                if curr_data_dir_path:
                    backup_dir_name = data_dirname + '.bk'
                    self.logger.print_msg('backup old dir "%s" to "%s"' % (curr_data_dir_path, backup_dir_name) )
                    #rcmd = 'test -d "{0}" || mkdir -p "{0}";mv "{1}" "{0}"'.format(backup_dir_name, curr_data_dir_path)
                    rcmd = 'test -d "{0}" && rm -rf "{0}";mv "{1}" "{0}"'.format(backup_dir_name, curr_data_dir_path)
                    self.__remote_cmd(host, (rcmd_go2web, rcmd))

            self.logger.print_msg('\r\n' + self.logger.color_str('ok', 'done.') + '\r\n' * 3)
            
    def __remote_cmd (self, remote_host, rcmd, onOutputChange = None):
        return remote_host and self.__cmd(rcmd, onOutputChange, remote_host) or False

    def __cmd (self, cmd, onOutputChange = None, remote_host = None):
        # cmd should be a "command string" or "tuple or list contains command strings" 
        if not isinstance(cmd, (str, tuple, list)):
            return False

        # remote_host exists ? => send remtoe command
        cmd = isinstance(cmd, (tuple, list)) and ';'.join(cmd) or cmd # join commands with ';' for tuple and list
        self.debug and ((not remote_host) and self.logger.print_msg('sh: ' + cmd) or self.logger.print_msg('rsh: ' + cmd)) # local or remote ?
        cmd = (not remote_host) and cmd or ('ssh -o StrictHostKeyChecking=no %s "%s"' % (remote_host, cmd)) # local or remote ?
        proc = subprocess.Popen(cmd, shell = True, stdin = subprocess.PIPE, stdout = subprocess.PIPE, stderr = subprocess.PIPE)
        output = [];
        while proc.poll() is None:
            lines = proc.communicate()[0].splitlines()
            if len(lines):
                for s in lines:
                    output.append(s)
                    self.debug and self.logger.print_msg(s.strip()) # debug mode ?
                    onOutputChange and onOutputChange(s) # trigger event 'OutputChange'
        
        return len(output) and string.join(output, '') or False

    # write XML documetn object to XML file
    def __write2xml (self, r_path, name, doc):
        if (isinstance(doc, Document)):
            xml_str = doc.toprettyxml(indent='', newl = '', encoding = 'UTF-8')
            FILE = open(os.path.join(self.config.get_data_path(), r_path, '%s.xml' % (name)), 'w+')
            FILE.writelines(xml_str)
            FILE.close()

    def __write2json (self, r_path, name, data):
        if isinstance(data, basestring):
            FILE = open(os.path.join(self.config.get_data_path(), r_path, '%s.json' % (name)), 'w+')
            FILE.writelines(data)
            FILE.close()

    def __fetch_dir_handler(self, data_r_paths=[], is_clear=False):
        if (not (len(data_r_paths))):
            return        
        
        data_path = self.config.get_data_path()
        
        #check root path fist
        if not os.path.exists(data_path):
            os.makedirs(data_path)
            if self.debug:
                self.logger.print_msg(' * [%s]make dir: %s' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), data_path))

        for r_path in data_r_paths:
            path = os.path.join(data_path, r_path)
            if not os.path.exists(path):
                # not exists ? => create
                os.makedirs(path)
                if self.debug:
                    self.logger.print_msg(' * [%s]make dir: %s' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), path))
            else:
                if is_clear:
                    # clear existing data files
                    os.system('rm -rf ' + path)
                    if self.debug:
                        self.logger.print_msg(' * [%s]clear dir: %s' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), path))
                    os.makedirs(path)
                    if self.debug:
                        self.logger.print_msg(' * [%s]make dir: %s' % (strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), path))



    def __get_ip_by_name (self, hostname = ''):
        ip_list = []
        result = socket.getaddrinfo(hostname, None)
        if isinstance(result, list):
            for r in result:
                (
                      isinstance(r, tuple)
                  and 5 == len(r)
                  and 2 == len(r[4])
                  and r[4][0] not in ip_list
                  and ip_list.append(str(r[4][0]).strip())
                )

        return ip_list
        

class CmsConfig():

    def __init__(self):
        self.start_time = datetime.datetime.fromtimestamp(time.time()).strftime('%Y%m%d%H%M%S') 

        self.base_path = os.path.dirname(__file__)
        config_file_path = os.path.join(self.base_path, 'config.ini')

        self.config_data = ConfigParser.ConfigParser()
        self.config_data.read(config_file_path)

        # set default languge codes
        self.lang_codes = ('EN', 'FR', 'RU', 'ES', 'PT_BR', 'ZH_TW', 'ZH_CN', 'KO', 'CS', 'DE', 'HU', 'IT', 'NL', 'PL', 'SV', 'JA')

    def get_session_id (self):
        return self.start_time

    def get_base_path (self):
        return self.base_path

    def get_data_dirname (self):
        return self.config_data.get('BASIC', 'data_dirname')

    def get_data_path (self):
        return os.path.join(self.base_path, self.get_data_dirname())

    def get_log_path (self):
        return os.path.join(self.base_path, 'logs')

    def get_data_backup_path (self):
        return os.path.join(self.base_path, 'backup')

    def get_all_lang_codes (self):
        return self.lang_codes

    def get_console_mode (self):
        return self.config_data.get('BASIC', 'console_mode')
    
    def get_db_config (self):
        return {
            'host': self.config_data.get('BASIC', 'db_host')
          , 'port': self.config_data.get('BASIC', 'db_port')
          , 'user': self.config_data.get('BASIC', 'db_user')
          , 'password': self.config_data.get('BASIC', 'db_password')
          , 'database': self.config_data.get('BASIC', 'db_name')
        }
        
    def get_ftp_config (self):
        return {
            'host': self.config_data.get('BASIC', 'ftp_host')
          , 'port': self.config_data.get('BASIC', 'ftp_port')
          , 'user': self.config_data.get('BASIC', 'ftp_user')
          , 'password': self.config_data.get('BASIC', 'ftp_password')
        }

    def get_smtp_config (self):
        return {
            'send_enable': self.config_data.get('NOTIFY', 'send_notify')
          , 'host': self.config_data.get('NOTIFY', 'smtp_host')
          , 'port': self.config_data.get('NOTIFY', 'smtp_port')
#          , 'user': self.config_data.get('NOTIFY', 'smtp_user')
#          , 'password': self.config_data.get('NOTIFY', 'smtp_password')
          , 'mail_to': self.config_data.get('NOTIFY', 'mail_to')
        }

    def is_send_notify_enabled (self):
        return (self.config_data.get('NOTIFY', 'send_notify')=='1')

    def get_sync_sites (self):
        return self.config_data.items('SITES')

        
class SendNotify():

    def __init__(self, configHandler):
        self.config = configHandler

        self.isNotifyEnabled = self.config.is_send_notify_enabled()        
        if self.isNotifyEnabled==True:        
            self.session_id = self.config.get_session_id()            
            self.smtp_info = self.config.get_smtp_config()
            
            self.session_log_file = os.path.join(self.config.get_log_path(), "%s.log" % (self.config.get_session_id()))
            self.smtpserver =  smtplib.SMTP(self.smtp_info['host'], int(self.smtp_info['port']))  

    def send_sync_notify(self, is_error_terminated):
        if self.isNotifyEnabled==False:
            return

        if is_error_terminated:
            title = "[ERROR] CMS Sync Notification - %s"%(self.session_id)
        else:
            title = "[SUCCESS] CMS Sync Notification - %s"%(self.session_id)

        msg = MIMEMultipart('alternative')
        msg['Subject'] = title
        msg['From'] = 'notification'
        msg['To'] = self.smtp_info['mail_to']

        text_body = """
Dear admins,
Please see attached log for CMS sync result.         
"""
        txt = MIMEText(text_body, 'plain', _charset='utf-8')
        msg.attach(txt)
             
        fp = open(self.session_log_file , 'rb')
        attach = MIMEImage(fp.read(), _subtype='plain')
        fp.close()
        attach.add_header('Content-Disposition', 'attachment', filename=os.path.basename(self.session_log_file))
        msg.attach(attach)

        self.smtpserver.sendmail(msg['From'] , msg["To"].split(","), msg.as_string())

    def close(self):
        if self.isNotifyEnabled==False:
            return
        self.smtpserver.quit()        

class LogWriter():

    def __init__(self, configHandler):
        self.config = configHandler
        self.session_log_file = os.path.join(self.config.get_log_path(), "%s.log" % (self.config.get_session_id()))        
        
    def color_str (self, type, str):
        # color codes
        COLOR_OKGREEN = ''
        COLOR_WARNING = ''
        COLOR_END = ''        
        if self.config.get_console_mode()=='1':
            COLOR_OKGREEN = '\033[92m'
            COLOR_WARNING = '\033[91m'
            COLOR_END = '\033[0m'
        return {
              'ok': COLOR_OKGREEN + str + COLOR_END
            , 'warning': COLOR_WARNING + str + COLOR_END
        }[type];
    
    def sep_line (self, str):
        return '%s [%s] %s %s' % ('=' * 10, strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), str, '=' * 10)

    # print message and append to 'logs'
    def print_msg (self, msg):
        print msg
        # write logs
        try:
            f = open(self.session_log_file, 'a+')
            f.write(msg + "\r\n");
            f.close
            return True
        except:
            print self.logger.color_str('warning', 'Can not write above message into log file.')
            return False
        
if __name__ == "__main__":
    sys.argv.pop(0) # ignore first item of argv

    cms = CmsSync()    
    cms.run(sys.argv)
