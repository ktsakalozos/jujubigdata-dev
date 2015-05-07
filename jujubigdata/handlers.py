# Copyright 2014-2015 Canonical Limited.
#
# This file is part of jujubigdata.
#
# jujubigdata is free software: you can redistribute it and/or modify
# it under the terms of the Apache License version 2.0.
#
# jujubigdata is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# Apache License for more details.

import re
from subprocess import check_output
import time

from path import Path

import jujuresources

from charmhelpers.core import host
from charmhelpers.core import hookenv
from charmhelpers.core import unitdata
from charmhelpers.core.charmframework import helpers

from jujubigdata import utils


class HadoopBase(object):
    def __init__(self, dist_config):
        self.dist_config = dist_config
        self.charm_config = hookenv.config()
        self.cpu_arch = host.cpu_arch()

        # dist_config will have simple validation done on primary keys in the
        # dist.yaml, but we need to ensure deeper values are present.
        required_dirs = ['hadoop', 'hadoop_conf', 'hdfs_log_dir',
                         'yarn_log_dir']
        missing_dirs = set(required_dirs) - set(self.dist_config.dirs.keys())
        if missing_dirs:
            raise ValueError('dirs option in {} is missing required entr{}: {}'.format(
                self.dist_config.yaml_file,
                'ies' if len(missing_dirs) > 1 else 'y',
                ', '.join(missing_dirs)))

        self.client_spec = {
            'hadoop': self.dist_config.hadoop_version,
        }
        self.verify_conditional_resources = utils.verify_resources('hadoop-%s' % self.cpu_arch)

    def spec(self):
        """
        Generate the full spec for keeping charms in sync.

        NB: This has to be a callback instead of a plain property because it is
        passed to the relations during construction of the Manager but needs to
        properly reflect the Java version in the same hook invocation that installs
        Java.
        """
        java_version = unitdata.kv().get('java.version')
        if java_version:
            return {
                'vendor': self.dist_config.vendor,
                'hadoop': self.dist_config.hadoop_version,
                'java': java_version,
                'arch': self.cpu_arch,
            }
        else:
            return None

    def is_installed(self):
        return unitdata.kv().get('hadoop.base.installed')

    def install(self, force=False):
        if not force and self.is_installed():
            return
        self.configure_hosts_file()
        self.dist_config.add_users()
        self.dist_config.add_dirs()
        self.dist_config.add_packages()
        self.install_base_packages()
        self.setup_hadoop_config()
        self.configure_hadoop()
        unitdata.kv().set('hadoop.base.installed', True)
        unitdata.kv().flush(True)

    def configure_hosts_file(self):
        """
        Add the unit's private-address to /etc/hosts to ensure that Java
        can resolve the hostname of the server to its real IP address.
        """
        private_address = hookenv.unit_get('private-address')
        hostname = check_output(['hostname']).strip()
        hostfqdn = check_output(['hostname', '-f']).strip()
        etc_hosts = Path('/etc/hosts')
        hosts = etc_hosts.lines()
        line = '%s %s %s' % (private_address, hostfqdn, hostname)
        IP_pat = r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'
        if not re.match(IP_pat, private_address):
            line = '# %s  # private-address did not return an IP' % line
        if hosts[0] != line:
            hosts.insert(0, line)
            etc_hosts.write_lines(hosts)

    def install_base_packages(self):
        with utils.disable_firewall():
            self.install_java()
            self.install_hadoop()

    def install_java(self):
        """
        Run the java-installer resource to install Java and determine
        the JAVA_HOME and Java version.

        The java-installer must be idempotent and its only output (on stdout)
        should be two lines: the JAVA_HOME path, and the Java version, respectively.

        If there is an error installing Java, the installer should exit
        with a non-zero exit code.
        """
        env = utils.read_etc_env()
        java_installer = Path(jujuresources.resource_path('java-installer'))
        java_installer.chmod(0o755)
        output = check_output([java_installer], env=env)
        java_home, java_version = map(str.strip, output.strip().split('\n'))
        unitdata.kv().set('java.home', java_home)
        unitdata.kv().set('java.version', java_version)

    def install_hadoop(self):
        jujuresources.install('hadoop-%s' %
                              self.cpu_arch,
                              destination=self.dist_config.path('hadoop'),
                              skip_top_level=True)

    def setup_hadoop_config(self):
        # copy default config into alternate dir
        conf_dir = self.dist_config.path('hadoop') / 'etc/hadoop'
        self.dist_config.path('hadoop_conf').rmtree_p()
        conf_dir.copytree(self.dist_config.path('hadoop_conf'))
        (self.dist_config.path('hadoop_conf') / 'slaves').remove_p()
        mapred_site = self.dist_config.path('hadoop_conf') / 'mapred-site.xml'
        if not mapred_site.exists():
            (self.dist_config.path('hadoop_conf') / 'mapred-site.xml.template').copy(mapred_site)

    def configure_hadoop(self):
        java_home = Path(unitdata.kv().get('java.home'))
        java_bin = java_home / 'bin'
        hadoop_bin = self.dist_config.path('hadoop') / 'bin'
        hadoop_sbin = self.dist_config.path('hadoop') / 'sbin'
        with utils.environment_edit_in_place('/etc/environment') as env:
            env['JAVA_HOME'] = java_home
            if java_bin not in env['PATH']:
                env['PATH'] = ':'.join([env['PATH'], java_bin])
            if hadoop_bin not in env['PATH']:
                env['PATH'] = ':'.join([env['PATH'], hadoop_bin])
            if hadoop_sbin not in env['PATH']:
                env['PATH'] = ':'.join([env['PATH'], hadoop_sbin])
            env['HADOOP_LIBEXEC_DIR'] = self.dist_config.path('hadoop') / 'libexec'
            env['HADOOP_INSTALL'] = self.dist_config.path('hadoop')
            env['HADOOP_HOME'] = self.dist_config.path('hadoop')
            env['HADOOP_COMMON_HOME'] = self.dist_config.path('hadoop')
            env['HADOOP_HDFS_HOME'] = self.dist_config.path('hadoop')
            env['HADOOP_MAPRED_HOME'] = self.dist_config.path('hadoop')
            env['HADOOP_YARN_HOME'] = self.dist_config.path('hadoop')
            env['YARN_HOME'] = self.dist_config.path('hadoop')
            env['HADOOP_CONF_DIR'] = self.dist_config.path('hadoop_conf')
            env['YARN_CONF_DIR'] = self.dist_config.path('hadoop_conf')
            env['YARN_LOG_DIR'] = self.dist_config.path('yarn_log_dir')
            env['HDFS_LOG_DIR'] = self.dist_config.path('hdfs_log_dir')
            env['HADOOP_LOG_DIR'] = self.dist_config.path('hdfs_log_dir')  # for hadoop 2.2.0 only
            env['MAPRED_LOG_DIR'] = '/var/log/hadoop/mapred'  # should be moved to config, but could
            env['MAPRED_PID_DIR'] = '/var/run/hadoop/mapred'  # be destructive for mapreduce operation

        hadoop_env = self.dist_config.path('hadoop_conf') / 'hadoop-env.sh'
        utils.re_edit_in_place(hadoop_env, {
            r'export JAVA_HOME *=.*': 'export JAVA_HOME=%s' % java_home,
        })

    def run(self, user, command, *args, **kwargs):
        """
        Run a Hadoop command as the `hdfs` user.

        :param str command: Command to run, prefixed with `bin/` or `sbin/`
        :param list args: Additional args to pass to the command
        """
        return utils.run_as(user,
                            self.dist_config.path('hadoop') / command,
                            *args, **kwargs)


class HDFS(object):
    def __init__(self, hadoop_base):
        self.hadoop_base = hadoop_base

    def stop_namenode(self):
        self._hadoop_daemon('stop', 'namenode')

    def start_namenode(self):
        if not utils.jps('NameNode'):
            self._hadoop_daemon('start', 'namenode')
            # Some hadoop processes take a bit of time to start
            # we need to let them get to a point where they are
            # ready to accept connections - increase the value for hadoop 2.4.1
            time.sleep(30)

    def stop_secondarynamenode(self):
        self._hadoop_daemon('stop', 'secondarynamenode')

    def start_secondarynamenode(self):
        if not utils.jps('SecondaryNameNode'):
            self._hadoop_daemon('start', 'secondarynamenode')
            # Some hadoop processes take a bit of time to start
            # we need to let them get to a point where they are
            # ready to accept connections - increase the value for hadoop 2.4.1
            time.sleep(30)

    def stop_datanode(self):
        self._hadoop_daemon('stop', 'datanode')

    def start_datanode(self):
        if not utils.jps('DataNode'):
            self._hadoop_daemon('start', 'datanode')

    def _remote(self, relation):
        # If we're relating the client, hdfs-secondary, or yarn-master to
        # hdfs-master, we'll be called during the namenode relation. If
        # relating compute-slave to hdfs-master, we'll be called during the
        # datanode relation.
        unit, data = helpers.any_ready_unit(relation)
        return data['private-address'], data['port']

    def _local(self):
        host = hookenv.unit_get('private-address')
        port = self.hadoop_base.dist_config.port('namenode')
        return host, port

    def configure_namenode(self):
        self.configure_hdfs_base(*self._local())
        cfg = self.hadoop_base.charm_config
        dc = self.hadoop_base.dist_config
        hdfs_site = dc.path('hadoop_conf') / 'hdfs-site.xml'
        with utils.xmlpropmap_edit_in_place(hdfs_site) as props:
            props['dfs.replication'] = cfg['dfs_replication']
            props['dfs.blocksize'] = int(cfg['dfs_blocksize'])
            props['dfs.namenode.datanode.registration.ip-hostname-check'] = 'true'
            props['dfs.namenode.http-address'] = '0.0.0.0:{}'.format(dc.port('nn_webapp_http'))
            # TODO: support SSL
            #props['dfs.namenode.https-address'] = '0.0.0.0:{}'.format(dc.port('nn_webapp_https'))

    def configure_secondarynamenode(self):
        """
        Configure the Secondary Namenode when the hadoop-hdfs-secondary
        charm is deployed and related to hadoop-hdfs-master.

        The only purpose of the secondary namenode is to perform periodic
        checkpoints. The secondary name-node periodically downloads current
        namenode image and edits log files, joins them into new image and
        uploads the new image back to the (primary and the only) namenode.
        """
        self.configure_hdfs_base(*self._remote("namenode"))

    def configure_datanode(self):
            self.configure_hdfs_base(*self._remote("datanode"))
            dc = self.hadoop_base.dist_config
            hdfs_site = dc.path('hadoop_conf') / 'hdfs-site.xml'
            with utils.xmlpropmap_edit_in_place(hdfs_site) as props:
                props['dfs.datanode.http.address'] = '0.0.0.0:{}'.format(dc.port('dn_webapp_http'))
                # TODO: support SSL
                #props['dfs.datanode.https.address'] = '0.0.0.0:{}'.format(dc.port('dn_webapp_https'))

    def configure_client(self):
        self.configure_hdfs_base(*self._remote("namenode"))

    def configure_hdfs_base(self, host, port):
        dc = self.hadoop_base.dist_config
        core_site = dc.path('hadoop_conf') / 'core-site.xml'
        with utils.xmlpropmap_edit_in_place(core_site) as props:
            props['fs.defaultFS'] = "hdfs://{host}:{port}".format(host=host, port=port)
            props['hadoop.proxyuser.hue.hosts'] = "*"
            props['hadoop.proxyuser.hue.groups'] = "*"
            props['hadoop.proxyuser.oozie.groups'] = '*'
            props['hadoop.proxyuser.oozie.hosts'] = '*'
        hdfs_site = dc.path('hadoop_conf') / 'hdfs-site.xml'
        with utils.xmlpropmap_edit_in_place(hdfs_site) as props:
            props['dfs.webhdfs.enabled'] = "true"
            props['dfs.namenode.name.dir'] = dc.path('hdfs_dir_base') / 'cache/hadoop/dfs/name'
            props['dfs.datanode.data.dir'] = dc.path('hdfs_dir_base') / 'cache/hadoop/dfs/name'
            props['dfs.permissions'] = 'false'  # TODO - secure this hadoop installation!

    def format_namenode(self):
        if unitdata.kv().get('hdfs.namenode.formatted'):
            return
        self.stop_namenode()
        # Run without prompting; this will fail if the namenode has already
        # been formatted -- we do not want to reformat existing data!
        self._hdfs('namenode', '-format', '-noninteractive')
        unitdata.kv().set('hdfs.namenode.formatted', True)
        unitdata.kv().flush(True)

    def create_hdfs_dirs(self):
        if unitdata.kv().get('hdfs.namenode.dirs.created'):
            return
        self._hdfs('dfs', '-mkdir', '-p', '/tmp/hadoop/mapred/staging')
        self._hdfs('dfs', '-chmod', '-R', '1777', '/tmp/hadoop/mapred/staging')
        self._hdfs('dfs', '-mkdir', '-p', '/tmp/hadoop-yarn/staging')
        self._hdfs('dfs', '-chmod', '-R', '1777', '/tmp/hadoop-yarn')
        self._hdfs('dfs', '-mkdir', '-p', '/user/ubuntu')
        self._hdfs('dfs', '-chown', '-R', 'ubuntu', '/user/ubuntu')
        # for JobHistory
        self._hdfs('dfs', '-mkdir', '-p', '/mr-history/tmp')
        self._hdfs('dfs', '-chmod', '-R', '1777', '/mr-history/tmp')
        self._hdfs('dfs', '-mkdir', '-p', '/mr-history/done')
        self._hdfs('dfs', '-chmod', '-R', '1777', '/mr-history/done')
        self._hdfs('dfs', '-chown', '-R', 'mapred:hdfs', '/mr-history')
        self._hdfs('dfs', '-mkdir', '-p', '/app-logs')
        self._hdfs('dfs', '-chmod', '-R', '1777', '/app-logs')
        self._hdfs('dfs', '-chown', 'yarn', '/app-logs')
        unitdata.kv().set('hdfs.namenode.dirs.created', True)
        unitdata.kv().flush(True)

    def register_slaves(self):
        slaves = helpers.all_ready_units('datanode')
        slaves_file = self.hadoop_base.dist_config.path('hadoop_conf') / 'slaves'
        slaves_file.write_lines(
            [
                '# DO NOT EDIT',
                '# This file is automatically managed by Juju',
            ] + [
                data['hostname'] for slave, data in slaves
            ]
        )
        slaves_file.chown('ubuntu', 'hadoop')

    def _hadoop_daemon(self, command, service):
        self.hadoop_base.run('hdfs', 'sbin/hadoop-daemon.sh',
                             '--config',
                             self.hadoop_base.dist_config.path('hadoop_conf'),
                             command, service)

    def _hdfs(self, command, *args):
        self.hadoop_base.run('hdfs', 'bin/hdfs', command, *args)


class YARN(object):
    def __init__(self, hadoop_base):
        self.hadoop_base = hadoop_base

    def stop_resourcemanager(self):
        self._yarn_daemon('stop', 'resourcemanager')

    def start_resourcemanager(self):
        if not utils.jps('ResourceManager'):
            self._yarn_daemon('start', 'resourcemanager')

    def stop_jobhistory(self):
        self._jobhistory_daemon('stop', 'historyserver')

    def start_jobhistory(self):
        if utils.jps('JobHistoryServer'):
            self._jobhistory_daemon('stop', 'historyserver')
        self._jobhistory_daemon('start', 'historyserver')

    def stop_nodemanager(self):
        self._yarn_daemon('stop', 'nodemanager')

    def start_nodemanager(self):
        if not utils.jps('NodeManager'):
            self._yarn_daemon('start', 'nodemanager')

    def _remote(self, relation):
        # If we're relating client to yarn-master, we'll be called during the
        # resourcemanager relation. If relating compute-slave to yarn-master,
        # we'll be called during the nodemanager relation.
        unit, data = helpers.any_ready_unit(relation)
        return data['private-address'], data['port']

    def _local(self):
        host = '0.0.0.0'
        port = self.hadoop_base.dist_config.port('resourcemanager')
        return host, port

    def configure_resourcemanager(self):
        self.configure_yarn_base(*self._local())
        dc = self.hadoop_base.dist_config
        yarn_site = dc.path('hadoop_conf') / 'yarn-site.xml'
        with utils.xmlpropmap_edit_in_place(yarn_site) as props:
            # 0.0.0.0 will listen on all interfaces, which is what we want on the server
            props['yarn.resourcemanager.webapp.address'] = '0.0.0.0:{}'.format(dc.port('rm_webapp_http'))
            # TODO: support SSL
            #props['yarn.resourcemanager.webapp.https.address'] = '0.0.0.0:{}'.format(dc.port('rm_webapp_https'))

    def configure_jobhistory(self):
        self.configure_yarn_base(*self._local())
        dc = self.hadoop_base.dist_config
        mapred_site = dc.path('hadoop_conf') / 'mapred-site.xml'
        with utils.xmlpropmap_edit_in_place(mapred_site) as props:
            # 0.0.0.0 will listen on all interfaces, which is what we want on the server
            props["mapreduce.jobhistory.address"] = "0.0.0.0:{}".format(dc.port('jobhistory'))
            props["mapreduce.jobhistory.webapp.address"] = "0.0.0.0:{}".format(dc.port('jh_webapp_http'))

    def configure_nodemanager(self):
        self.configure_yarn_base(*self._remote("nodemanager"))

    def configure_client(self):
        self.configure_yarn_base(*self._remote("resourcemanager"))

    def configure_yarn_base(self, host, port):
        dc = self.hadoop_base.dist_config
        yarn_site = dc.path('hadoop_conf') / 'yarn-site.xml'
        with utils.xmlpropmap_edit_in_place(yarn_site) as props:
            props['yarn.nodemanager.aux-services'] = 'mapreduce_shuffle'
            props['yarn.resourcemanager.hostname'] = '{}'.format(host)
            props['yarn.resourcemanager.address'] = '{}:{}'.format(host, port)
            props["yarn.log.server.url"] = "{}:{}/jobhistory/logs/".format(
                'localhost' if host == '0.0.0.0' else host, dc.port('rm_log'))
        mapred_site = dc.path('hadoop_conf') / 'mapred-site.xml'
        with utils.xmlpropmap_edit_in_place(mapred_site) as props:
            props["mapreduce.jobhistory.address"] = "{}:{}".format(host, dc.port('jobhistory'))
            props["mapreduce.framework.name"] = 'yarn'

    def install_demo(self):
        if unitdata.kv().get('yarn.client.demo.installed'):
            return
        # Copy our demo (TeraSort) to the target location and set mode/owner
        demo_source = 'scripts/terasort.sh'
        demo_target = '/home/ubuntu/terasort.sh'

        Path(demo_source).copy(demo_target)
        Path(demo_target).chmod(0o755)
        Path(demo_target).chown('ubuntu', 'hadoop')
        unitdata.kv().set('yarn.client.demo.installed', True)
        unitdata.kv().flush(True)

    def _yarn_daemon(self, command, service):
        self.hadoop_base.run('yarn', 'sbin/yarn-daemon.sh',
                             '--config',
                             self.hadoop_base.dist_config.path('hadoop_conf'),
                             command, service)

    def _jobhistory_daemon(self, command, service):
        # TODO refactor job history to separate class
        self.hadoop_base.run('mapred', 'sbin/mr-jobhistory-daemon.sh',
                             '--config',
                             self.hadoop_base.dist_config.path('hadoop_conf'),
                             command, service)