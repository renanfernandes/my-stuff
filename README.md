# my-stuff

My templates, scripts and configuration files to make my life easier.

Table of Contents
=================

<!--ts-->
* [macOS Stuff](#macOS-Stuff)
  * [Making terminal better](#making-terminal-better)
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
4. Add the following lines to ~/.bash_profile or ~/.bashrc
```alias ls='ls -G'
alias ll='ls -lG'
```
4. Apply the changes right away: `source ~/.bash_profile`
5. Voila!
