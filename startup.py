import os
import json
import pyplugin_installer
import re
import configparser
import requests
import pkg_resources
import subprocess

from qgis.core import (QgsSettings, QgsApplication, QgsAuthMethodConfig, QgsExpressionContextUtils,
                       QgsMessageLog, Qgis, QgsProviderRegistry, QgsDataSourceUri, QgsUserProfileManager,
                       QgsFavoritesItem, QgsColorScheme, QgsColorSchemeRegistry)
from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.utils import iface
from pyplugin_installer.installer_data import repositories, plugins, reposGroup


class StartupDSIUN:
    def __init__(self):

        #### Variables à paramétrer ####
        self.conf_url = "https://raw.githubusercontent.com/naub1n/QGIS_DSIUN_Startup/master/startup_parameters.json"
        self.default_profile_message = "Vous utilisez le profil par défaut. Privilégiez le profil DSIUN."
        self.qgis_bad_version_message = "Vous utilisez une version (%s) non gérée par la DSIUN (%s). Le paramètrage ne sera pas appliqué."

        #################################

        self.pyplugin_inst = pyplugin_installer.instance()
        pyplugin_installer.instance().fetchAvailablePlugins(True)
        self.plugins_data = pyplugin_installer.installer_data.plugins
        # Un bug dans userProfileManager() dans les versions inférieures à 3.30.0 ne permet pas d'interagir correctement
        # avec les profils utilisateurs : https://github.com/qgis/QGIS/issues/48337
        self.qgis_min_version_profile = 33000
        self.auth_mgr = QgsApplication.authManager()
        self.current_v = Qgis.QGIS_VERSION_INT

        if self.current_v >= self.qgis_min_version_profile:
            self.p_mgr = iface.userProfileManager()
        else:
            self.p_mgr = QgsUserProfileManager()

        self.current_profile_path = None
        self.profiles_path = None
        self.config_profiles_path = None

        # Préparation des chemins vers les dossiers ou fichiers de config qgis
        self.get_paths()
        # Lecture du fichier de configuration
        self.global_config = self.get_global_config()


    def get_env(self):
        profile_name = self.get_current_profile_name()
        if profile_name == "DSIUN_dev":
            return "dev"
        
        elif profile_name in ["DSIUN_rec", "DSIUN_int"]:
            return "rec"
        
        elif profile_name == "DSIUN":
            return "prd"
        
        else:
            return ""

    def get_global_config(self):
        self.log("Lecture de la configuration globale.", Qgis.Info)
        try:
            conf_url = self.conf_url
            resp = requests.get(conf_url, verify=False)
            config = resp.json()

            return config

        except Exception as e:
            self.log("Erreur lors de la lecture de la configuration globale: %s" % str(e), Qgis.Critical)

            return {}

    # def read_conf(self, config):
    #     self.log("Lecture de la configuration de l'environnement.", Qgis.Info)
    #     if config == {}:
    #         self.log("Erreur lors de la lecture de la configuration de l'environnement", Qgis.Critical)
    #         return {}
    #     else:
    #         env_config = {}
    #         current_profile = self.get_current_profile_name()
    #         for key, environment in enumerate(config.get("environments", [])):
    #             if environment.get("env_name", "") == "production":
    #                 env_prod_key = key

    #             if environment.get("profile_name", "") == current_profile:
    #                 env_config = environment
    #                 env_name = environment.get("env_name", "")

    #         if not env_config:
    #             self.log("profil '%s' inconnu, utilisation de la configuration de production" % current_profile,
    #                      Qgis.Warning)
    #             env_config = config.get("environments", [])[env_prod_key]
    #         else:
    #             self.log("Utilisation de la configuration '%s'" % env_name, Qgis.Info)

    #         return env_config

    def start(self):
        if self.check_json() and self.global_config:
            profiles = self.global_config.get("profiles", [])

            if self.check_version():
                profile_name = self.get_current_profile_name()
                if profile_name in profiles:
                    self.add_custom_env_vars()
                    self.open_network_drives()
                    self.check_repo()
                    self.check_auth_cfg()
                    self.install_plugins()
                    self.set_plugins_config()
                    self.add_db_connections()
                    self.add_favorites()
                    self.add_wfs_connections()
                    self.add_wms_connections()
                    self.add_arcgis_connections()
                    self.add_layout_templates()
                    self.add_svg_paths()
                    self.set_default_crs()
                    self.set_global_settings()
                    self.add_colors()
                    self.check_profiles()
                else:
                    self.check_profiles()

                if profile_name == 'default':
                    iface.messageBar().pushMessage(self.default_profile_message, level=Qgis.Info, duration=10)

    def log(self, log_message, log_level):
        QgsMessageLog.logMessage(log_message, 'Startup DSIUN', level=log_level, notifyUser=False)

    def check_repo(self):
        self.log("Vérification des dépôts de plugins ...", Qgis.Info)
        repos = self.global_config.get("plugins", {}).get("repositories", [])

        for repo in repos:
            repo_name = repo.get("name", "")
            repo_url = repo.get("url", "")
            repo_authcfg = repo.get("authcfg", "")
            repo_envs = [x.lower() for x in repo.get("envs", [])]

            if self.check_envs(repo_envs):
                if repo_name and repo_url:
                    try:
                        settings = QgsSettings()
                        settings.beginGroup(reposGroup)
                        if repo_name in repositories.all():
                            settings.remove(repo_name)
                        # add to settings
                        settings.setValue(repo_name + "/url", repo_url)
                        settings.setValue(repo_name + "/authcfg", repo_authcfg)
                        settings.setValue(repo_name + "/enabled", True)
                        # refresh lists and populate widgets
                        plugins.removeRepository(repo_name)
                        self.pyplugin_inst.reloadAndExportData()
                        self.log("Ajout/Remplacement du dépôt %s - OK" % repo_name, Qgis.Info)
                    except Exception as e:
                        self.log("Erreur lors de la l'ajout/le remplacement du dépôt %s : %s" % (repo_name, str(e)), Qgis.Critical)

    def install_plugins(self):
        self.log("Vérification des plugins requis ...", Qgis.Info)
        try:
            plugins = []
            plugins_items = self.global_config.get("plugins", {}).get("plugins_names", [])
            # Ajout des plugins dans la liste de ceux à installer sur l'utilisateur fait partie du bon domain
            for item in plugins_items:
                plugins_list = item.get("names", [])
                plugins_domains = [x.lower() for x in item.get("domains", [])]
                plugins_users = [x.lower() for x in item.get("users", [])]
                plugins_envs = [x.lower() for x in item.get("envs", [])]

                if self.check_envs(plugins_envs) and self.check_users_and_domains(plugins_users, plugins_domains):
                    plugins.extend(plugins_list)

            available_plugins_keys = self.plugins_data.all().keys()
            upgradable_plugins_keys = self.plugins_data.allUpgradeable().keys()

            errors = False

            for plugin in plugins:

                if plugin in available_plugins_keys:

                    is_installed = self.plugins_data.all()[plugin]['installed']
                    is_upgradable = plugin in upgradable_plugins_keys

                    if not is_installed or is_upgradable:
                        self.log("Installation/Mise à jour du paquet %s" % str(plugin), Qgis.Info)
                        self.pyplugin_inst.installPlugin(plugin)
                    else:
                        settings = QgsSettings()
                        plugin_is_active = settings.value("/PythonPlugins/" + plugin, False, type=bool)
                        
                        if not plugin_is_active:
                            self.log("Activation du plugin %s" % str(plugin), Qgis.Info)
                            settings.setValue( "PythonPlugins/" + plugin, True)

                        self.log("Le paquet %s est installé et à jour" % str(plugin), Qgis.Info)
                else:
                    errors = True
                    self.log("Le paquet %s est indisponible" % str(plugin), Qgis.Critical)

            if not errors:
                self.log("Vérification des plugins - OK", Qgis.Info)
        except Exception as e:
            self.log("Erreur lors l'installation ou les mise à jour des plugins : %s" % str(e), Qgis.Critical)

    def set_plugins_config(self):
        self.log("Configuration des plugins", Qgis.Info)
        plugins_configs = self.global_config.get("plugins", {}).get("configs", [])
        for config in plugins_configs:
            plugin_name = config.get("plugin_name", "")
            plugin_config_domains = [x.lower() for x in config.get("domains", [])]
            plugin_config_users = [x.lower() for x in config.get("users", [])]
            plugin_config_envs = [x.lower() for x in config.get("envs", [])]

            if self.check_envs(plugin_config_envs) and self.check_users_and_domains(plugin_config_users, plugin_config_domains):
                if plugin_name == "custom_catalog":
                    catalogs = config.get("config", {}).get("catalogs", [])
                    self.get_catalog_config(catalogs)

                if plugin_name == "menu_from_project":
                    self.set_menu_from_project_config(config.get("config", {}))

                if plugin_name == "creer_menus":
                    self.set_creer_menus_config(config.get("config", {}))

                if plugin_name == "custom_news_feed":
                    self.set_customnewsfeed_config(config.get("config", {}))


    def get_current_customcatalog_settings(self):
        s = QgsSettings()
        s.beginGroup("CustomCatalog/catalogs")

        catalogs = []
        for key in s.childGroups():
            catalog = {
                "name": s.value("%s/name" % key, ""),
                "type": s.value("%s/type" % key, ""),
                "link": s.value("%s/link" % key, ""),
                "qgisauthconfigid": s.value("%s/qgisauthconfigid" % key, "")
            }
            catalogs.append(catalog)

        settings = {"catalogs": catalogs}

        return settings

    def save_customcatalog_settings(self, settings):
        s = QgsSettings()
        s.beginGroup("CustomCatalog")
        s.remove("CustomCatalog/catalogs")

        for key, catalog in enumerate(settings.get("catalogs", [])):
            s.setValue("catalogs/%s/name" % key, catalog.get("name", ""))
            s.setValue("catalogs/%s/type" % key, catalog.get("type", ""))
            s.setValue("catalogs/%s/link" % key, catalog.get("link", ""))
            s.setValue("catalogs/%s/qgisauthconfigid" % key, catalog.get("qgisauthconfigid", ""))

    def get_catalog_config(self, catalogs):
        self.log("Paramétrage du plugin custom_catalog", Qgis.Info)
        try:
            plugin_name = 'custom_catalog'
            # Vérification de la disponibilité du plugin
            if plugin_name in self.plugins_data.all().keys():
                # Vérification de son installation
                if self.plugins_data.all()[plugin_name]['installed']:
                    catalog_settings = self.get_current_customcatalog_settings()

                    # Suppression du catalog par défaut
                    for index, catalog in enumerate(catalog_settings['catalogs']):
                        if catalog['name'] == 'CatalogExample':
                            self.log("Suppression du catalogue par défaut", Qgis.Info)
                            del catalog_settings['catalogs'][index]
                            break

                    for catalog in catalogs:
                        new_catalog_name = catalog.get("name", "")
                        new_catalog_data = {
                            "name": new_catalog_name,
                            "type": catalog.get("type", ""),
                            "link": catalog.get("link", ""),
                            "qgisauthconfigid": catalog.get("qgisauthconfigid", "")
                        }
                        if not any(local_catalog.get('name', None) == new_catalog_name for local_catalog in
                                   catalog_settings['catalogs']):
                            self.log("Catalogue '%s' absent - ajout du catalogue" % new_catalog_name, Qgis.Info)
                            catalog_settings['catalogs'].append(new_catalog_data)
                        else:
                            self.log("Mise à jour du catalogue '%s'." % new_catalog_name, Qgis.Info)
                            for key, local_catalog in enumerate(catalog_settings['catalogs']):
                                if local_catalog.get("name", "") == new_catalog_name:
                                    catalog_settings['catalogs'][key] = new_catalog_data

                    self.save_customcatalog_settings(catalog_settings)

                    self.log("Paramétrage du catalogue - OK", Qgis.Info)
                else:
                    self.log("Le plugin %s n'est pas installé" % str(plugin_name), Qgis.Warning)
            else:
                self.log("Le plugin %s n'est pas disponible" % str(plugin_name), Qgis.Warning)
        except Exception as e:
            self.log("Erreur lors du paramétrage du catalogue : %s" % str(e), Qgis.Critical)

    def check_auth_cfg(self):
        self.log("Vérification de la configuration d'authentification des AE ...", Qgis.Info)
        try:
            auth_configs = self.global_config.get("authentications", [])

            ids = self.auth_mgr.availableAuthMethodConfigs().keys()

            for auth_config in auth_configs:
                self.auth_id = auth_config.get("id", "")
                self.auth_conf_name = auth_config.get("name", "")
                auth_user = auth_config.get("user", "")
                auth_pass = auth_config.get("pass", "")
                auth_domains = [x.lower() for x in auth_config.get("domains", [])]
                auth_users = [x.lower() for x in auth_config.get("users", [])]
                auth_envs = [x.lower() for x in auth_config.get("envs", [])]

                if self.check_envs(auth_envs) and self.check_users_and_domains(auth_users, auth_domains):
                    if self.auth_id in ids:
                        self.log("La configuration %s est déjà présente" % str(self.auth_id), Qgis.Info)

                    else:
                        self.log("Ajout de la configuration d'authentification %s" % str(self.auth_id), Qgis.Info)

                        if auth_user and auth_pass:
                            self.save_auth_config(self.auth_id, self.auth_conf_name, auth_user, auth_pass)

                            continue

                        else:
                            self.qt_auth_dlg = QtWidgets.QDialog(None)
                            self.qt_auth_dlg.setFixedWidth(450)
                            self.qt_auth_dlg.setWindowTitle("Indiquer votre login et mdp")

                            self.qt_auth_login = QtWidgets.QLineEdit(self.qt_auth_dlg)
                            self.qt_auth_login.setPlaceholderText("Identifiant")

                            if self.auth_id in ["dsiun01"]:
                                self.qt_auth_login.setText(self.get_user_email())

                            if auth_user:
                                self.qt_auth_login.setText(auth_user)

                            self.qt_auth_pass = QtWidgets.QLineEdit(self.qt_auth_dlg)
                            self.qt_auth_pass.setEchoMode(QtWidgets.QLineEdit.Password)
                            self.qt_auth_pass.setPlaceholderText("Mot de passe (laisser vide si inconnu)")

                            if auth_pass:
                                self.qt_auth_pass.setText(auth_pass)

                            self.qt_info_auth_conf_name = QtWidgets.QLabel(self.qt_auth_dlg)
                            self.qt_info_auth_conf_name.setText("Nom : %s" % self.auth_conf_name)

                            button_save = QtWidgets.QPushButton('Enregistrer', self.qt_auth_dlg)
                            button_save.clicked.connect(self.button_save_clicked)

                            layout = QtWidgets.QVBoxLayout(self.qt_auth_dlg)
                            layout.addWidget(self.qt_info_auth_conf_name)
                            layout.addWidget(self.qt_auth_login)
                            layout.addWidget(self.qt_auth_pass)
                            layout.addWidget(button_save)

                            self.qt_auth_dlg.setLayout(layout)
                            self.qt_auth_dlg.setWindowModality(Qt.WindowModal)
                            self.qt_auth_dlg.exec_()

            self.log("Vérification de la configuration des authentifications - OK", Qgis.Info)
        except Exception as e:
            self.log("Erreur lors de la vérification de la configuration d'authentification : %s" % str(e),
                     Qgis.Critical)

    def button_save_clicked(self):
        self.save_auth_config(self.auth_id, self.auth_conf_name, self.qt_auth_login.text(), self.qt_auth_pass.text())
        self.qt_auth_dlg.accept()

    def save_auth_config(self, auth_id, name, user, password):
        config = QgsAuthMethodConfig()
        config.setId(auth_id)
        config.setName(name)
        config.setConfig('username', user)
        config.setConfig('password', password)
        config.setMethod("Basic")
        assert config.isValid()

        self.auth_mgr.storeAuthenticationConfig(config)

    # def check_profiles(self):
    #     self.log("Vérification des profiles ...", Qgis.Info)
    #     try:
    #         for env in self.global_config.get("environments", []):
    #             profile = env.get("profile_name", "")
    #             is_default_profile = env.get("profile_default", False)

    #             qgis_profile_path = os.path.join(self.profiles_path, profile + '/QGIS')
    #             config_profile_path = os.path.join(qgis_profile_path, 'QGIS3.ini').replace('\\', '/')

    #             if not self.profile_exists(profile):
    #                 self.log("Création du profile %s" % profile, Qgis.Info)
    #                 if self.current_v >= self.qgis_min_version_profile:
    #                     self.p_mgr.createUserProfile(profile)
    #                 else:
    #                     self.log("Utilisation de la méthode pour les versions inférieures à %s" %
    #                              self.qgis_min_version_profile, Qgis.Info)
    #                     # Création du dossier du profil et du fichier de configuration vide
    #                     os.makedirs(qgis_profile_path, exist_ok=True)
    #                     with open(config_profile_path, mode='w'):
    #                         pass

    #                 if is_default_profile:
    #                     # Demande à l'utilisateur d'ouvrir le profile de la DSIUN
    #                     msg_box = QtWidgets.QMessageBox()
    #                     msg_box.setText("Le profile %s a été créé.\n" % profile +
    #                                     "Souhaitez-vous basculer sur ce profil?")
    #                     msg_box.setStandardButtons(QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Yes)
    #                     open_new_profil = msg_box.exec()
    #                     if open_new_profil == QtWidgets.QMessageBox.Yes:
    #                         self.p_mgr.loadUserProfile(profile)
    #                         # Ne fonctionne pas :
    #                         os._exit(0)

    #             # Vérification du profil par défaut
    #             if is_default_profile:
    #                 if not self.p_mgr.defaultProfileName == profile:
    #                     self.set_default_profile(profile)

    #         self.log("Vérification des profiles - OK", Qgis.Info)
    #     except Exception as e:
    #         self.log("Erreur lors de la vérification des profiles : %s" % str(e), Qgis.Critical)

    def check_profiles(self):
        self.log("Vérification des profiles ...", Qgis.Info)
        try:
            profiles = self.global_config.get("profiles", [])
            for profile in profiles:
                qgis_profile_path = os.path.join(self.profiles_path, profile + '/QGIS')
                config_profile_path = os.path.join(qgis_profile_path, 'QGIS3.ini').replace('\\', '/')

                if not self.profile_exists(profile):
                    self.log("Création du profile %s" % profile, Qgis.Info)
                    if self.current_v >= self.qgis_min_version_profile:
                        self.p_mgr.createUserProfile(profile)
                    else:
                        self.log("Utilisation de la méthode pour les versions inférieures à %s" %
                                 self.qgis_min_version_profile, Qgis.Info)
                        # Création du dossier du profil et du fichier de configuration vide
                        os.makedirs(qgis_profile_path, exist_ok=True)
                        with open(config_profile_path, mode='w'):
                            pass
                        
            if self.get_env() == "":
                self.log("Le profil utilisé n'est pas administré par la DSIUN", Qgis.Warning)

        except Exception as e:
            self.log("Erreur lors de la vérification des profiles : %s" % str(e), Qgis.Critical)

    def set_default_profile(self, profile_name):
        self.log("Paramétrage du profile par défaut ...", Qgis.Info)
        try:
            if self.current_v >= self.qgis_min_version_profile:
                self.p_mgr.setDefaultProfileName(profile_name)
            else:
                self.log("Utilisation de la méthode pour les versions inférieures à %s" %
                         self.qgis_min_version_profile, Qgis.Info)
                config = configparser.ConfigParser()
                config.read(self.config_profiles_path)
                config['core']['defaultProfile'] = profile_name
                with open(self.config_profiles_path, 'w') as configfile:
                    config.write(configfile, space_around_delimiters=False)

            self.log("%s devient le profile par défaut" % profile_name, Qgis.Info)
        except Exception as e:
            self.log("Impossible de définir '%s' en tant que profil par défaut : %s" % (str(profile_name), str(e)),
                     Qgis.Critical)

    def check_version(self):
        self.log("Vérification de la version de QGIS ...", Qgis.Info)
        qgis_version_dsiun = self.global_config.get("qgis", {}).get("dsiun_version", "")
        if self.current_v == qgis_version_dsiun:
            return True
        else:
            iface.messageBar().pushMessage(self.qgis_bad_version_message % (self.current_v, qgis_version_dsiun),
                                           level=Qgis.Warning, duration=10)
            self.log(self.qgis_bad_version_message % (self.current_v, qgis_version_dsiun), Qgis.Warning)
            return False

    def get_paths(self):
        self.current_profile_path = QgsApplication.qgisSettingsDirPath().replace('\\', '/')
        self.profiles_path = re.search("(.*?profiles/)", self.current_profile_path).group(1)
        self.config_profiles_path = os.path.join(self.profiles_path, 'profiles.ini')

    def get_current_profile_name(self):
        # Les versions inférieures de QGIS ont un bug sur la gestion des profils
        if self.current_v >= self.qgis_min_version_profile:
            profile = self.p_mgr.userProfile()
            profile_name = profile.name()
        else:
            profile_name = re.search("profiles/(.*?)/", self.current_profile_path).group(1)

        return profile_name

    def profile_exists(self, profile_name):
        if self.current_v >= self.qgis_min_version_profile:
            return self.p_mgr.profileExists(profile_name)

        else:
            return profile_name in os.listdir(self.profiles_path)

    def add_db_connections(self):
        self.log("Vérification de la présence des connections aux BDD.", Qgis.Info)
        database_connections = self.global_config.get("db_connections", [])
        for cnx in database_connections:
            cnx_provider = cnx.get("qgis_provider", "")
            cnx_name = cnx.get("name", "")
            cnx_host = cnx.get("host", "")
            cnx_port = cnx.get("port", "")
            cnx_dbname = cnx.get("dbname", "")
            cnx_auth_id = cnx.get("auth_id", "")
            cnx_configs = cnx.get("configs", {})
            cnx_domains = [x.lower() for x in cnx.get("domains", [])]
            cnx_users = [x.lower() for x in cnx.get("users", [])]
            cnx_envs = [x.lower() for x in cnx.get("envs", [])]

            if self.check_envs(cnx_envs) and self.check_users_and_domains(cnx_users, cnx_domains):
                try:
                    provider = QgsProviderRegistry.instance().providerMetadata(cnx_provider)
                    uri = QgsDataSourceUri()
                    uri.setConnection(aHost=cnx_host,
                                      aPort=cnx_port,
                                      aDatabase=cnx_dbname,
                                      aUsername=None,
                                      aPassword=None,
                                      authConfigId=cnx_auth_id)
                    
                    config = {
                        "allowGeometrylessTables": cnx_configs.get("allowGeometrylessTables", False),
                        "dontResolveType": cnx_configs.get("dontResolveType", False),
                        "estimatedMetadata": cnx_configs.get("estimatedMetadata", False),
                        "geometryColumnsOnly": cnx_configs.get("geometryColumnsOnly", True), 
                        "metadataInDatabase": cnx_configs.get("metadataInDatabase", False),
                        "projectsInDatabase": cnx_configs.get("projectsInDatabase", False),
                        "publicOnly": cnx_configs.get("publicOnly", False),
                        "savePassword": cnx_configs.get("savePassword", False),
                        "saveUsername": cnx_configs.get("saveUsername", False) 
                    }
                    self.log("Ajout/Restauration de la connection '%s'." % cnx_name, Qgis.Info)
                    new_conn = provider.createConnection(None, config)
                    new_conn.setUri(uri.uri(expandAuthConfig=False))
                    # La connexion est ajoutée si inexistante ou remplacée si elle existe déjà
                    new_conn.store(cnx_name)
                except Exception as e:
                    self.log("Erreur lors de la l'ajout de la connexion '%s' : %s" % (cnx_name, str(e)), Qgis.Critical)

    def add_favorites(self):
        self.log("Vérification des marques-pages.", Qgis.Info)
        favorites = self.global_config.get("favorites", [])

        for favorite in favorites:
            f_path = favorite.get("path", "")
            f_name = favorite.get("name", f_path)
            f_domains = [x.lower() for x in favorite.get("domains", [])]
            f_users = [x.lower() for x in favorite.get("users", [])]
            f_envs = [x.lower() for x in favorite.get("envs", [])]

            if self.check_envs(f_envs) and self.check_users_and_domains(f_users, f_domains):
                fi = QgsFavoritesItem(None, "")

                # Vérification de la présence du marque-page
                for item in fi.createChildren():
                    if item.name() == f_name:
                        fi.removeDirectory(item)

                try:
                    self.log("Ajout/Restauration du marque-page '%s'." % f_path, Qgis.Info)
                    fi.addDirectory(f_path, f_name)
                except Exception as e:
                    self.log("Erreur lors de la l'ajout du marque-page '%s' : %s" % (f_path, str(e)), Qgis.Critical)

    def add_wfs_connections(self):
        self.log("Vérification des connexions WFS.", Qgis.Info)
        wfs_connections = self.global_config.get("wfs_connections", [])

        for wfs_c in wfs_connections:
            cnx_name = wfs_c.get("name", "")
            cnx_ignoreaxisorientation = wfs_c.get("ignoreAxisOrientation", False)
            cnx_invertaxisorientation = wfs_c.get("invertAxisOrientation", False)
            cnx_maxnumfeatures = wfs_c.get("maxnumfeatures", "")
            cnx_pagesize = wfs_c.get("pagesize", 1000)
            cnx_pagingenabled = wfs_c.get("pagingenabled", True)
            cnx_prefercoordinatesforwfst11 = wfs_c.get("preferCoordinatesForWfsT11", False)
            cnx_url = wfs_c.get("url", "")
            cnx_version = wfs_c.get("version", "auto")
            cnx_authcfg = wfs_c.get("authcfg", "")
            cnx_username = wfs_c.get("username", "")
            cnx_password = wfs_c.get("password", "")
            cnx_domains = [x.lower() for x in wfs_c.get("domains", [])]
            cnx_users = [x.lower() for x in wfs_c.get("users", [])]
            cnx_envs = [x.lower() for x in wfs_c.get("envs", [])]

            if self.check_envs(cnx_envs) and self.check_users_and_domains(cnx_users, cnx_domains):
                s = QgsSettings()
                s.beginGroup('qgis')
                s.beginGroup('connections-wfs')

                s.setValue("%s/ignoreAxisOrientation" % cnx_name, cnx_ignoreaxisorientation)
                s.setValue("%s/invertAxisOrientation" % cnx_name, cnx_invertaxisorientation)
                s.setValue("%s/maxnumfeatures" % cnx_name, cnx_maxnumfeatures)
                s.setValue("%s/pagesize" % cnx_name, cnx_pagesize)
                s.setValue("%s/pagingenabled" % cnx_name, cnx_pagingenabled)
                s.setValue("%s/preferCoordinatesForWfsT11" % cnx_name, cnx_prefercoordinatesforwfst11)
                s.setValue("%s/url" % cnx_name, cnx_url)
                s.setValue("%s/version" % cnx_name, cnx_version)

                s = QgsSettings()
                s.beginGroup('qgis')
                s.beginGroup('WFS')
                s.setValue("%s/authcfg" % cnx_name, cnx_authcfg)
                s.setValue("%s/username" % cnx_name, cnx_username)
                s.setValue("%s/password" % cnx_name, cnx_password)


    def add_wms_connections(self):
        self.log("Vérification des connexions WMS/WMTS.", Qgis.Info)
        wms_connections = self.global_config.get("wms_connections", [])

        for wms_c in wms_connections:
            cnx_name = wms_c.get("name", "")
            cnx_dpimode = wms_c.get("dpiMode", 7)
            cnx_ignoreaxisorientation = wms_c.get("ignoreAxisOrientation", False)
            cnx_ignoregetfeatureinfouri = wms_c.get("ignoreGetFeatureInfoURI", False)
            cnx_ignoregetmapuri = wms_c.get("ignoreGetMapURI", False)
            cnx_invertaxisorientation = wms_c.get("invertAxisOrientation", False)
            cnx_reportedlayerextents = wms_c.get("reportedLayerExtents", False)
            cnx_smoothpixmaptransform = wms_c.get("smoothPixmapTransform", False)
            cnx_url = wms_c.get("url", "")
            cnx_authcfg = wms_c.get("authcfg", "")
            cnx_username = wms_c.get("username", "")
            cnx_password = wms_c.get("password", "")
            cnx_domains = [x.lower() for x in wms_c.get("domains", [])]
            cnx_users = [x.lower() for x in wms_c.get("users", [])]
            cnx_envs = [x.lower() for x in wms_c.get("envs", [])]

            if self.check_envs(cnx_envs) and self.check_users_and_domains(cnx_users, cnx_domains):
                s = QgsSettings()
                s.beginGroup('qgis')
                s.beginGroup('connections-wms')

                s.setValue("%s/dpiMode" % cnx_name, cnx_dpimode)
                s.setValue("%s/ignoreAxisOrientation" % cnx_name, cnx_ignoreaxisorientation)
                s.setValue("%s/invertAxisOrientation" % cnx_name, cnx_invertaxisorientation)
                s.setValue("%s/ignoreGetFeatureInfoURI" % cnx_name, cnx_ignoregetfeatureinfouri)
                s.setValue("%s/ignoreGetMapURI" % cnx_name, cnx_ignoregetmapuri)
                s.setValue("%s/reportedLayerExtents" % cnx_name, cnx_reportedlayerextents)
                s.setValue("%s/smoothPixmapTransform" % cnx_name, cnx_smoothpixmaptransform)
                s.setValue("%s/url" % cnx_name, cnx_url)

                s = QgsSettings()
                s.beginGroup('qgis')
                s.beginGroup('WMS')

                s.setValue("%s/authcfg" % cnx_name, cnx_authcfg)
                s.setValue("%s/username" % cnx_name, cnx_username)
                s.setValue("%s/password" % cnx_name, cnx_password)

    def add_arcgis_connections(self):
        self.log("Vérification des connexions ArcGIS Server.", Qgis.Info)
        arcgis_connections = self.global_config.get("arcgis_connections", [])

        for arcgis_c in arcgis_connections:
            cnx_name = arcgis_c.get("name", "")
            cnx_headers = arcgis_c.get("headers", [])
            cnx_url = arcgis_c.get("url", "")
            cnx_community_endpoint = arcgis_c.get("community_endpoint", "")
            cnx_content_endpoint = arcgis_c.get("content_endpoint", "")
            cnx_authcfg = arcgis_c.get("authcfg", "")
            cnx_username = arcgis_c.get("username", "")
            cnx_password = arcgis_c.get("password", "")
            cnx_domains = [x.lower() for x in arcgis_c.get("domains", [])]
            cnx_users = [x.lower() for x in arcgis_c.get("users", [])]
            cnx_envs = [x.lower() for x in arcgis_c.get("envs", [])]

            if self.check_envs(cnx_envs) and self.check_users_and_domains(cnx_users, cnx_domains):
                s = QgsSettings()
                s.beginGroup('qgis')
                s.beginGroup('connections-arcgisfeatureserver')

                s.setValue("%s/url" % cnx_name, cnx_url)
                s.setValue("%s/community_endpoint" % cnx_name, cnx_community_endpoint)
                s.setValue("%s/content_endpoint" % cnx_name, cnx_content_endpoint)

                for header in cnx_headers:
                    s.setValue("%s/http-header/%s" % (cnx_name, header.get("key")), header.get("value"))

                s = QgsSettings()
                s.beginGroup('qgis')
                s.beginGroup('ARCGISFEATURESERVER')

                s.setValue("%s/authcfg" % cnx_name, cnx_authcfg)
                s.setValue("%s/username" % cnx_name, cnx_username)
                s.setValue("%s/password" % cnx_name, cnx_password)

    def add_layout_templates(self):
        self.log("Vérification des modèles.", Qgis.Info)
        layout_templates = self.global_config.get("composer_templates", [])

        for template in layout_templates:
            t_path = template.get("path", "")
            t_domains = [x.lower() for x in template.get("domains", [])]
            t_users = [x.lower() for x in template.get("users", [])]
            t_envs = [x.lower() for x in template.get("envs", [])]

            layouts = QgsApplication.layoutTemplatePaths()

            if t_path and self.check_users_and_domains(t_users, t_domains) and self.check_envs(t_envs):

                if t_path not in layouts:
                    layouts.append(t_path)

                    s = QgsSettings()
                    s.beginGroup('core')
                    s.beginGroup('Layout')

                    s.setValue("searchPathsForTemplates", layouts)

    def add_svg_paths(self):
        self.log("Ajout des symboles SVG.", Qgis.Info)
        svg_paths = self.global_config.get("svg_paths", [])

        for item in svg_paths:
            svg_path = item.get("path", "")
            svg_domains = [x.lower() for x in item.get("domains", [])]
            svg_users = [x.lower() for x in item.get("users", [])]
            svg_envs = [x.lower() for x in item.get("envs", [])]

            qgs_svg_paths = QgsApplication.svgPaths()

            if svg_path and self.check_users_and_domains(svg_users, svg_domains) and self.check_envs(svg_envs):
                if svg_path not in qgs_svg_paths:
                    qgs_svg_paths.append(svg_path)
                    QgsApplication.setDefaultSvgPaths(qgs_svg_paths)

    def add_custom_env_vars(self):
        self.log("Définition des variables d'environnement personnalisées", Qgis.Info)
        custom_env_vars = self.global_config.get("custom_env_vars", [])

        s = QgsSettings()
        s.beginGroup('qgis')
        s.setValue("customEnvVarsUse", True)

        for var in custom_env_vars:
            var_name = var.get("name", "")
            var_value = var.get("value", "")
            var_domains = [x.lower() for x in var.get("domains", [])]
            var_users = [x.lower() for x in var.get("users", [])]
            var_envs = [x.lower() for x in var.get("envs", [])]

            if var_name and self.check_users_and_domains(var_users, var_domains) and self.check_envs(var_envs):
                current_custom_vars = s.value("customEnvVars", [])
                if isinstance(current_custom_vars, str):
                    current_custom_vars = [current_custom_vars]

                new_custom_vars = []

                for current_var in current_custom_vars:
                    if "|" + var_name + "=" not in current_var:
                        new_custom_vars.append(current_var)

                new_custom_vars.append("overwrite|%s=%s" % (var_name, var_value))

                s.setValue("customEnvVars", new_custom_vars)



    def check_json(self):
        if self.install_python_package("jsonschema"):
            import jsonschema
        else:
            msg = "Le paquet jsonschema n'a pas pû être installé, la validation est ignorée"
            self.log(msg, Qgis.Warning)
            return True

        schema_conf_url = self.global_config.get('$schema',
                                                 'https://raw.githubusercontent.com/naub1n/QGIS_DSIUN_Startup/master/startup_parameters_schema.json')

        try:
            r = requests.get(schema_conf_url,
                             headers={'Accept': 'application/json'},
                             verify=False)
            schema = r.json()

        except Exception as e:
            msg = "Impossible de lire l'URL du schéma : %s" % str(e)
            self.log(msg, Qgis.Warning)
            msg = "La validation est ignorée"
            self.log(msg, Qgis.Warning)
            return True

        if schema:
            try:
                jsonschema.validate(self.global_config, schema)
                self.log("Configuration JSON validée", Qgis.Info)
                return True

            except Exception as valid_err:
                msg = "La configuration JSON n'est pas valide:\n%s" % str(valid_err)
                self.log(msg, Qgis.Critical)
                return False

        else:
            self.log("Le schéma de validation est vide - Validation ignorée", Qgis.Warning)
            return True

    def install_python_package(self, package_name):
        installed_packages = pkg_resources.working_set
        installed_packages_list = sorted([i.key for i in installed_packages])
        if package_name not in installed_packages_list:
            self.log("Installation de %s" % package_name, Qgis.Info)
            import subprocess
            osgeo4w_env_path = os.path.join(os.getenv('OSGEO4W_ROOT'), 'OSGeo4W.bat')
            try:
                subprocess.check_call(['call', osgeo4w_env_path, ';',
                                       'python.exe', '-m', 'pip', 'install', '--upgrade', 'pip'], shell=True)
            except Exception as e:
                msg = "Impossible de mettre à jour Pip : %s" % str(e)
                self.log(msg, Qgis.Warning)
                return False

            try:
                subprocess.check_call(['call', osgeo4w_env_path, ';',
                                       'python.exe', '-m', 'pip', 'install', package_name], shell=True)
            except Exception as e:
                msg = "Impossible d'installer le paquet %s : %s" % (package_name, str(e))
                self.log(msg, Qgis.Warning)
                return False

        return True

    def check_users_and_domains(self, users=[], domains=[]):
        user = os.environ.get("username", "").lower()
        domain = os.environ.get("userdomain", "").lower()
        if domain in domains or "all" in domains or user in users:
            return True
        else:
            return False
        
    def check_envs(self, envs):
        env = self.get_env()
        if env in envs or "all" in envs:
            return True
        else:
            return False


    def set_menu_from_project_config(self, config):
        self.log("Paramétrage du plugin menu_from_project", Qgis.Info)

        options = config.get("options", {})

        s = QgsSettings()
        s.beginGroup('menu_from_project')

        s.setValue("is_setup_visible", options.get("is_setup_visible", True))
        s.setValue("optionTooltip", options.get("optionTooltip", True))
        s.setValue("optionCreateGroup", options.get("optionCreateGroup", False))
        s.setValue("optionLoadAll", options.get("optionLoadAll", False))
        s.setValue("optionSourceMD", options.get("optionSourceMD", "ogc"))

        projects = config.get("projects", [])

        # Liste des noms de projets à ajouter (ils seront placés au début)
        project_names = []
        for project in projects:
            project_names.append(project.get("name", ""))

        # Lecture des projets existants
        current_projects = []
        cs = QgsSettings()
        cs.beginGroup('menu_from_project')
        cs.beginGroup("projects")
        for current_project_id in cs.childGroups():
            cs.beginGroup(current_project_id)
            # Si le nom du projet ne fait pas partie de ceux à ajouter alors il est conservé
            cpn = cs.value("name")
            if not cpn in project_names:
                project = {
                    "file": cs.value("file"),
                    "location": cs.value("location"),
                    "name": cpn,
                    "type_storage": cs.value("type_storage")
                }
                current_projects.append(project)
                self.log("Le projet '%s' est conservé" % cpn, Qgis.Info)

            cs.endGroup()

        s.remove("projects")

        if not config.get("replace_projects", False):
            projects.extend(current_projects)

        for key, project in enumerate(projects, start=1):
            s.setValue("projects/%s/%s" % (key, "file"), project.get("file", ""))
            s.setValue("projects/%s/%s" % (key, "location"), project.get("location", ""))
            s.setValue("projects/%s/%s" % (key, "name"), project.get("name", ""))
            s.setValue("projects/%s/%s" % (key, "type_storage"), project.get("type_storage", ""))

        s.beginGroup("projects")
        s.setValue("size" , len(s.childGroups()))

    def set_creer_menus_config(self, config):
        self.log("Paramétrage du plugin créer menu", Qgis.Info)

        file_menus = config.get("fileMenus", "")

        if file_menus:
            s = QgsSettings()
            s.setValue("PluginCreerMenus/fileMenus", file_menus)

    def set_customnewsfeed_config(self, config):
        self.log("Paramétrage du plugin CustomNewsFeed", Qgis.Info)

        config_path = config.get("config_path", "")

        if config_path:
            s = QgsSettings()
            s.setValue("CustomNewsFeed/json_file_path", config_path)

    def set_default_crs(self):
        self.log("Paramétrage du système de projection par défaut", Qgis.Info)

        default_crs = self.global_config.get("default_crs", "EPSG:4326")

        s = QgsSettings()
        s.setValue("Projections/layerDefaultCrs", default_crs)
        s.setValue("app/projections/defaultProjectCrs", default_crs)
        s.setValue("app/projections/unknownCrsBehavior", "UseDefaultCrs")
        s.setValue("app/projections/newProjectCrsBehavior", "UsePresetCrs")
        s.setValue("app/projections/crsAccuracyWarningThreshold", 0.0)

    def set_global_settings(self):
        self.log("Mise en place des paramètres globaux de QGIS", Qgis.Info)

        global_settings = self.global_config.get("qgis", {}).get("global_settings", [])

        s = QgsSettings()

        for global_setting in global_settings:
            settings = global_setting.get("settings", [])
            settings_domains = [x.lower() for x in global_setting.get("domains", [])]
            settings_users = [x.lower() for x in global_setting.get("users", [])]
            settings_envs = [x.lower() for x in global_setting.get("envs", [])]

            if self.check_envs(settings_envs) and self.check_users_and_domains(settings_users, settings_domains):
                for setting in settings:
                    setting_path = setting.get("path", "")
                    setting_value = setting.get("value", "")
                    if setting_path:
                        s.setValue(setting_path, setting_value)

    def get_user_email(self):
        if os.name == 'nt':
            email = subprocess.check_output(['whoami', '/upn'], shell=True, universal_newlines=True).split('\n')[0]

            return email

    def open_network_drives(self):
        self.log("Ouverture des lecteurs réseau pour les activer.", Qgis.Info)

        network_drives = self.global_config.get("network_drives", [])

        for nd in network_drives:
            drives = nd.get("drives", [])
            nd_domains = [x.lower() for x in nd.get("domains", [])]
            nd_users = [x.lower() for x in nd.get("users", [])]
            nd_envs = [x.lower() for x in nd.get("envs", [])]

            if self.check_envs(nd_envs) and self.check_users_and_domains(nd_users, nd_domains):
                for d in drives:
                    subprocess.run(["start","/min", "explorer", "%s" % d], shell=True)
                    subprocess.run(["C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe","-Command",'(New-Object -ComObject Shell.Application).Windows() | Where-Object{$_.Document.Folder.Self.Path -eq "%s" } | ForEach-Object{ $_.Quit() }' % d], shell=True)

    def add_colors(self):
        self.log("Ajout des palettes de couleurs", Qgis.Info)

        color_schemes = self.global_config.get("color_schemes", [])

        for color_scheme in color_schemes:
            cs_name = color_scheme.get("name", "")
            cs_colors = color_scheme.get("colors", [])
            cs_domains = [x.lower() for x in color_scheme.get("domains", [])]
            cs_users = [x.lower() for x in color_scheme.get("users", [])]
            cs_envs = [x.lower() for x in color_scheme.get("envs", [])]

            if self.check_envs(cs_envs) and self.check_users_and_domains(cs_users, cs_domains):
                new_color_scheme = NewColorScheme(name=cs_name, json_colors=cs_colors)
                csr = QgsApplication.colorSchemeRegistry()
                csr.addColorScheme(new_color_scheme)

class NewColorScheme(QgsColorScheme):
    def __init__(self, parent=None, name="", json_colors=[]):
        QgsColorScheme.__init__(self)
        self.name = name
        self.colors = json_colors

    def schemeName(self):
        return self.name

    def fetchColors(self,context='', basecolor=QColor()):
        new_colors = []
        for color in self.colors:
            new_color = []
            new_color.append(QColor(color.get("code", "#FFFFFF")))
            new_color.append(color.get("name", "unknown"))

            new_colors.append(new_color)

        return new_colors

    def flags(self):
        return QgsColorScheme.ShowInAllContexts

    def clone(self):
        return NewColorScheme()

# Lancement de la procédure
startup = StartupDSIUN()
startup.start()
