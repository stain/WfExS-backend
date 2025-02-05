# Install and setup a Workflow Execution Service backend instance

## Core Dependencies
This workflow execution service backend is written for Python 3.6 and later.

* In order to install the dependencies you need `pip` and `venv` modules.
	- `pip` is available in many Linux distributions (Ubuntu packages `python3-pip`, CentOS EPEL package `python-pip`), and also as [pip](https://pip.pypa.io/en/stable/) Python package.
	- `venv` is also available in many Linux distributions (Ubuntu package `python3-venv`). In some of them is integrated into the Python 3.5 (or later) installation.

* The creation of a virtual environment where to install WfExS backend dependencies is done running:
  
```bash
python3 -m venv .pyWEenv
source .pyWEenv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt
```

* If you upgrade your Python installation (from version 3.6 to 3.7, for instance), or you move this folder to a different location after following this instructions, you may need to remove and reinstall the virtual environment.

## Software Dependencies

As there are additional software dependencies beyond core ones, which are needed depending on the setup of the instance. There is an automated installer at [installer.bash](installer.bash), which assumes both python3 and its pip and venv counterparts are properly installed. It installs both core dependencies and it fetches and installs:

  * OpenJDK: needed by Nextflow.
  * gocryptfs: needed by secure directories feature.
  * A static bash copy: needed by Nextflow runner to monkey-patch some containers which do not have bash, or whose bash copy is buggy.

Depending on the local setup, some other external tools or container technologies are needed in several stages of the code. Please, install them, using either native packages (for instance, from your Linux distribution) or by hand and later set their path in the local configuration file you are using:

  * [encfs](https://vgough.github.io/encfs/) can be used for the feature of secure intermediate results. It has been tested with version 1.9.2 and 1.9.5 ([releases](https://github.com/vgough/encfs/releases) have to be compiled or installed from your distro).

  * [gocryptfs](https://nuetzlich.net/gocryptfs/) can be used for the feature of secure intermediate results. It has been tested since version v2.0-beta2 ([releases](https://github.com/rfjakob/gocryptfs/releases) provide static binaries).

  * [java](https://openjdk.java.net/): supported from version 8 to any version below 15 (Nextflow does not support this last one).
  
  * [git](https://git-scm.com/) is used to fetch workflows from git repositories.
  
  * [singularity](https://sylabs.io/singularity/): when local installation is set up to use singularity, 3.5 or later.
  
  * [docker](https://www.docker.com/): when local installation is set up to use docker. Not all the combinations of workflow execution engines and secure or paranoid setups support it.
  
  * [podman](https://podman.io/): when local installation is set up to use podman. Not all the combinations of workflow execution engines and secure or paranoid setups support it.

## Secure working directories additional requirements and incompatibilities

Currently, both Nextflow and cwltool support secure and paranoid working directories when no container technology is set up.

* When [Singularity](https://sylabs.io/singularity/) is set up, both Nextflow and cwltool support secure working directories when either singularity was compiled and set up with user namespaces support, or FUSE was set up at the system level in `/etc/fuse.conf` with the flag _`user_allow_other`_.

* When [Singularity](https://sylabs.io/singularity/) is set up, both Nextflow and cwltool support paranoid working directories when singularity was compiled and set up with user namespaces support.

* When Docker or Podman are set up, there is no support for secure or paranoid working directories due technical and architectural limitations.


## License
* © 2020-2021 Barcelona Supercomputing Center (BSC), ES

Licensed under the Apache License, version 2.0 <https://www.apache.org/licenses/LICENSE-2.0>, see the file `LICENSE.txt` for details.
