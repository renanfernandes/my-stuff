# my-stuff

My templates, scripts and configuration files to make my life easier.

Table of Contents
=================

<!--ts-->
* [macOS Stuff](#macOS-Stuff)
  * [Making terminal better](#making-terminal-better)
* [Integrating Sentinel and Home Infrastructure](#integrating-sentinel-and-home-infrastructure)
  * [Syslog server](#syslog-server)
    * [Provisioning the VM](#provisioning-the-vm)
    * [Configuring the Syslog Server](#configuring-the-syslog-server)
<!--te-->

# macOS Stuff
Configuration files and scripts to make my macOS better for day-to-day activities

## Making terminal better
Instead of having the ugly and non-intuitive standard terminal screen on macOS like the one below:
<img src="images/terminal_old.png" width="800">

Just a few changes will make it looking way better, like below:
<img src="images/terminal_new.png" width="800">

1. Download the Dracula Terminal color scheme from this Github repository: https://github.com/lysyi3m/macos-terminal-themes
2. Open the `dracula.terminal` file and set it as default on macOS
3. <img src="images/terminal_config.png" width="800">
4. Set background Opacity to 78%
5. <img src="images/terminal_config2.png" width="300">
6. On Window tab, set the terminal size to 100x30
7. <img src="images/terminal_config3.png" width="800">
8. Finally, on shell, set the Startup/Run Command to : `source ~/.profile ; reset`
9. <img src="images/terminal_config4.png" width="800">
10. Add the following lines to ~/.profile
```
alias ls='ls -G'
alias ll='ls -lG'
```
4. Apply the changes right away: `source ~/.bash_profile`
5. Voila!

# Integrating Sentinel and Home Infrastructure
As my home network continues to grow and more and more IoT devices are added, I felt the need to better improve my home security posture and frankly have a better visibilty over what is going on in my network.

To start, the diagram below shows how my home network is currently set up. I will focus on collecting the Netgate SG-1100 logs into Sentinel, for this, we first need a Syslog server

# Syslog server
First of all, we need a lace to host our Syslog server. The way it works is that your Firewall (in my case my Netgate SG-1100) will send our logs to my Syslog server, that wil eventually relay the logs to Azure Sentinel.

You can run either a Windows or Linux server for your Syslog server and you can host on premise or in the cloud. Since my final goal is to ship the logs to Sentinel, I decided to create a Linux VM and host in Azure.

This will allow expansion in the future and permit me connecting my servers overseas to this infrascture all well (but thats a topic for another discussion)

## Provisioning the VM
Since I am not doing anything fancy and all I need is to collect syslogs, I provisioned a single VM with Ubuntu 21.10 and used **Standard_B2s** size. Which gives me 2 vCPUs and 4 GB of RAM at East US 2 Region. Costing me approximately $30.37/month, not bad!

<img src="images/syslog-azure1.png" width="600">

Now that your VM is up, its time to configure Rsyslog Server

## Configuring the Syslog Server
First things first... Ensure you have the most up-to-date version of your packages and OS by running apt update:
```
sudo apt update
sudo apt upgrade
```


For consistency, lets ensure that your timeone is correct. In my case, America/New_York:
```
sudo timedatectl set-timezone America/New_York
```

**Announcement!!**
From now one, I'll rely on this amazing [Microsoft Tech Community Post](https://techcommunity.microsoft.com/t5/microsoft-sentinel/pfsense-syslog-to-azure-sentinel-guide/m-p/2004352) to configure and install elastic. You can follow the guide on the link or refer to my instructions below that provides some few changes to the config.

Ok, back to the instructions. First of all, we will need to install Logstash. For that, we need to first, Download and install the public GPG signing key:

```
wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | sudo apt-key add -
```

Then, we need to install apt-transport-https to allow apt to access https repositories (in this case, Elastic)

```
sudo apt-get install apt-transport-https
```

Finally, we will add the Elastic Repositories

```
echo "deb https://artifacts.elastic.co/packages/7.x/apt stable main" | sudo tee -a /etc/apt/sources.list.d/elastic-7.x.list
```

*Note: ElasticSearch 6.8.9+ and 7.8.9+ are not vulnerable to [Apache Log4j2 CVE-2021-44228 vulnerability](https://discuss.elastic.co/t/apache-log4j2-remote-code-execution-rce-vulnerability-cve-2021-44228-esa-2021-31/291476), so please make sure you are installing the latest supported version*

Now lets update apt repository and install Logstash:

```
sudo apt update
sudo apt install logstash
```

If everything went well, Logstash will be installed shortly after. You can check the status of logstash by looking at the daemon status:

```
sudo service logstash status
```

Now lets configure Logstash and apply the grok pattern.

First, create the directories:

```
sudo mkdir /etc/logstash/conf.d/{databases,patterns,templates}
```

Second, download the configuration files from this amazing [repository](https://github.com/noodlemctwoodle/pf-azure-sentinel/tree/main/Logstash-Configuration) maintained by noodlemctwoodle

```
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/01-inputs.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/02-types.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/03-filter.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/05-apps.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/20-interfaces.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/30-geoip.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/35-rules-desc.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/37-enhanced_user_agent.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/38-enhanced_url.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/45-cleanup.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/49-enhanced_private.conf -PO /etc/logstash/conf.d/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/50-outputs.conf -PO /etc/logstash/conf.d/
```

Now download the gronk patterns

```
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/patterns/pfelk.grok -P /etc/logstash/conf.d/patterns/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/patterns/openvpn.grok -P /etc/logstash/conf.d/patterns/
```

And the following configuration files.
```
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/36-ports-desc.conf -P /etc/logstash/conf.d/
```

Download the Databases..
```
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/databases/rule-names.csv -P /etc/logstash/conf.d/databases/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/databases/service-names-port-numbers.csv -P /etc/logstash/conf.d/databases/
sudo wget https://raw.githubusercontent.com/noodlemctwoodle/pfsense-azure-sentinel/main/Logstash-Configuration/etc/logstash/conf.d/databases/private-hostnames.csv -P /etc/logstash/conf.d/databases/
```

