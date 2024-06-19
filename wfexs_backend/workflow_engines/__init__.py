#!/usr/bin/env python
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: Apache-2.0
# Copyright 2020-2024 Barcelona Supercomputing Center (BSC), Spain
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import absolute_import

import os
import pathlib
import sys
import tempfile
import atexit
import shutil
import time
import abc
import glob
import logging

from typing import (
    cast,
    NamedTuple,
    TYPE_CHECKING,
)

from ..common import (
    AbstractWfExSException,
    ContainerType,
    ContentKind,
    DEFAULT_CONTAINER_TYPE,
    DEFAULT_DOCKER_CMD,
    DEFAULT_ENGINE_MODE,
    EngineMode,
    GeneratedContent,
    GeneratedDirectoryContent,
    MaterializedOutput,
)

if TYPE_CHECKING:
    from typing import (
        Any,
        Callable,
        Mapping,
        MutableSequence,
        NewType,
        Optional,
        Pattern,
        Sequence,
        Set,
        Tuple,
        Type,
        Union,
    )

    from typing_extensions import (
        Final,
        TypeAlias,
    )

    from ..common import (
        AbstractGeneratedContent,
        AbsPath,
        AnyPath,
        ContainerTaggedName,
        EngineVersion,
        ExitVal,
        ExpectedOutput,
        Fingerprint,
        LocalWorkflow,
        MaterializedInput,
        MaterializedContent,
        RelPath,
        StagedExecution,
        StagedSetup,
        SymbolicOutputName,
        SymbolicParamName,
        TRS_Workflow_Descriptor,
        URIType,
        WFLangVersion,
    )

    from ..container_factories import (
        Container,
        ContainerEngineVersionStr,
        ContainerFactory,
        ContainerOperatingSystem,
        ProcessorArchitecture,
    )

    EngineLocalConfig: TypeAlias = Mapping[str, Any]

    # This is also an absolute path
    EnginePath = NewType("EnginePath", AbsPath)

    WorkflowEngineVersionStr = NewType("WorkflowEngineVersionStr", str)

from ..container_factories.no_container import (
    NoContainerFactory,
)

from ..utils.contents import CWLDesc2Content, GetGeneratedDirectoryContent
from ..utils.digests import ComputeDigestFromFile, nihDigester

# Constants
WORKDIR_INPUTS_RELDIR = "inputs"
WORKDIR_EXTRAPOLATED_INPUTS_RELDIR = "extrapolated-inputs"
WORKDIR_INTERMEDIATE_RELDIR = "intermediate"
WORKDIR_META_RELDIR = "meta"
WORKDIR_STATS_RELDIR = "stats"
WORKDIR_OUTPUTS_RELDIR = "outputs"
WORKDIR_ENGINE_TWEAKS_RELDIR = "engineTweaks"
WORKDIR_WORKFLOW_RELDIR = "workflow"
WORKDIR_CONSOLIDATED_WORKFLOW_RELDIR = "consolidated-workflow"
WORKDIR_CONTAINERS_RELDIR = "containers"

WORKDIR_STDOUT_FILE = "stdout.txt"
WORKDIR_STDERR_FILE = "stderr.txt"

WORKDIR_WORKFLOW_META_FILE = "workflow_meta.yaml"

# This one is commented-out, as credentials SHOULD NEVER BE SAVED
# WORKDIR_SECURITY_CONTEXT_FILE = 'credentials.yaml'

WORKDIR_MARSHALLED_STAGE_FILE = "stage-state.yaml"
WORKDIR_MARSHALLED_EXECUTE_FILE = "execution-state.yaml"
WORKDIR_MARSHALLED_EXPORT_FILE = "export-state.yaml"
WORKDIR_PASSPHRASE_FILE = ".passphrase"

STATS_DAG_DOT_FILE = "dag.dot"


# Default priority
DEFAULT_PRIORITY: "Final[int]" = 0


class WorkflowType(NamedTuple):
    """
    engineName: symbolic name of the engine
    shortname: short name used in the WfExS-backend configuration files
    for the workflow language
    name: Textual representation of the workflow language
    clazz: Class implementing the engine invocation
    uriMatch: The URI patterns used in RO-Crate to identify the workflow type
    uriTemplate: The URI template to be used when RO-Crate ComputerLanguage is generated
    url: The URL used in RO-Crate to represent the workflow language
    trs_descriptor: The string used in GA4GH TRSv2 specification to define this workflow type
    rocrate_programming_language: Traditional internal id in RO-Crate implementations used for this workflow type (to be deprecated)
    """

    engineName: "str"
    shortname: "str"
    name: "str"
    clazz: "Type[AbstractWorkflowEngineType]"
    uriMatch: "Sequence[Union[Pattern[str], URIType]]"
    uriTemplate: "URIType"
    url: "URIType"
    trs_descriptor: "TRS_Workflow_Descriptor"
    rocrate_programming_language: "str"
    priority: "int" = DEFAULT_PRIORITY
    enabled: "bool" = True

    @classmethod
    def _value_fixes(cls) -> "Mapping[str, Optional[str]]":
        return {"shortname": "trs_descriptor"}

    @property
    def has_explicit_outputs(self) -> "bool":
        return self.clazz.HasExplicitOutputs()


class MaterializedWorkflowEngine(NamedTuple):
    """
    instance: Instance of the workflow engine
    version: Version of the engine to be used
    fingerprint: Fingerprint of the engine to be used (it could be the version)
    engine_path: Absolute path to the fetched engine
    workflow: Instance of LocalWorkflow
    containers_path: Where the containers are going to be available for offline-execute
    containers: List of Container instances (needed by workflow)
    operational_containers: List of Container instances (needed by engine)
    """

    instance: "AbstractWorkflowEngineType"
    version: "EngineVersion"
    fingerprint: "Union[Fingerprint, str]"
    engine_path: "EnginePath"
    workflow: "LocalWorkflow"
    containers_path: "Optional[AbsPath]" = None
    containers: "Optional[Sequence[Container]]" = None
    operational_containers: "Optional[Sequence[Container]]" = None


# This skeleton is here only for type mapping reasons
class AbstractWorkflowEngineType(abc.ABC):
    @classmethod
    @abc.abstractmethod
    def MyWorkflowType(cls) -> "WorkflowType":
        pass

    @classmethod
    @abc.abstractmethod
    def HasExplicitOutputs(cls) -> "bool":
        pass

    @property
    def workflowType(self) -> "WorkflowType":
        return self.MyWorkflowType()

    @abc.abstractmethod
    def getConfiguredContainerType(self) -> "ContainerType":
        pass

    @property
    def configuredContainerType(self) -> "ContainerType":
        return self.getConfiguredContainerType()

    @property
    @abc.abstractmethod
    def engine_url(self) -> "URIType":
        pass

    @abc.abstractmethod
    def _get_engine_version_str(
        self, matWfEng: "MaterializedWorkflowEngine"
    ) -> "WorkflowEngineVersionStr":
        """
        It must return a string in the form of
        "{symbolic engine name} {version}"
        """
        pass

    @abc.abstractmethod
    def sideContainers(self) -> "Sequence[ContainerTaggedName]":
        pass

    @abc.abstractmethod
    def materialize_containers(
        self,
        listOfContainerTags: "Sequence[ContainerTaggedName]",
        containersDir: "AnyPath",
        offline: "bool" = False,
        force: "bool" = False,
        injectable_containers: "Sequence[Container]" = [],
    ) -> "Tuple[ContainerEngineVersionStr, Sequence[Container], ContainerOperatingSystem, ProcessorArchitecture]":
        pass

    @abc.abstractmethod
    def deploy_containers(
        self,
        containers_list: "Sequence[Container]",
        containersDir: "Optional[AnyPath]" = None,
        force: "bool" = False,
    ) -> "Sequence[Container]":
        pass

    @property
    @abc.abstractmethod
    def staged_containers_dir(self) -> "AnyPath":
        pass

    @abc.abstractmethod
    def materializeEngine(
        self,
        localWf: "LocalWorkflow",
        engineVersion: "Optional[EngineVersion]" = None,
        do_identify: "bool" = False,
    ) -> "Optional[MaterializedWorkflowEngine]":
        pass

    @abc.abstractmethod
    def identifyWorkflow(
        self, localWf: "LocalWorkflow", engineVer: "Optional[EngineVersion]" = None
    ) -> "Union[Tuple[EngineVersion, LocalWorkflow], Tuple[None, None]]":
        """
        This method should return the effective engine version needed
        to run it when this workflow engine recognizes the workflow type
        """
        pass

    @abc.abstractmethod
    def materializeWorkflow(
        self,
        matWorfklowEngine: "MaterializedWorkflowEngine",
        consolidatedWorkflowDir: "AbsPath",
        offline: "bool" = False,
    ) -> "Tuple[MaterializedWorkflowEngine, Sequence[ContainerTaggedName]]":
        """
        Method to ensure the workflow has been materialized. It returns a
        possibly updated materialized workflow engine, as well as the list of containers

        For Nextflow it is usually a no-op, but for CWL it requires resolution
        """

        pass

    @abc.abstractmethod
    def launchWorkflow(
        self,
        matWfEng: "MaterializedWorkflowEngine",
        inputs: "Sequence[MaterializedInput]",
        environment: "Sequence[MaterializedInput]",
        outputs: "Sequence[ExpectedOutput]",
    ) -> "StagedExecution":
        pass

    @classmethod
    @abc.abstractmethod
    def FromStagedSetup(
        cls,
        staged_setup: "StagedSetup",
        container_factory_classes: "Sequence[Type[ContainerFactory]]" = [
            NoContainerFactory
        ],
        cache_dir: "Optional[AnyPath]" = None,
        cache_workflow_dir: "Optional[AnyPath]" = None,
        cache_workflow_inputs_dir: "Optional[AnyPath]" = None,
        local_config: "Optional[EngineLocalConfig]" = None,
        config_directory: "Optional[AnyPath]" = None,
    ) -> "AbstractWorkflowEngineType":
        pass


class WorkflowEngineException(AbstractWfExSException):
    """
    Exceptions fired by instances of WorkflowEngine
    """

    pass


class WorkflowEngine(AbstractWorkflowEngineType):
    def __init__(
        self,
        container_factory_clazz: "Type[ContainerFactory]" = NoContainerFactory,
        cacheDir: "Optional[AnyPath]" = None,
        workflow_config: "Optional[Mapping[str, Any]]" = None,
        local_config: "Optional[EngineLocalConfig]" = None,
        engineTweaksDir: "Optional[AnyPath]" = None,
        cacheWorkflowDir: "Optional[AnyPath]" = None,
        cacheWorkflowInputsDir: "Optional[AnyPath]" = None,
        workDir: "Optional[AnyPath]" = None,
        outputsDir: "Optional[AnyPath]" = None,
        outputMetaDir: "Optional[AnyPath]" = None,
        intermediateDir: "Optional[AnyPath]" = None,
        tempDir: "Optional[AnyPath]" = None,
        stagedContainersDir: "Optional[AnyPath]" = None,
        secure_exec: "bool" = False,
        allowOther: "bool" = False,
        config_directory: "Optional[AnyPath]" = None,
    ):
        """
        Abstract init method

        :param cacheDir:
        :param workflow_config:
            This one may be needed to identify container overrides
            or specific engine versions
        :param local_config:
        :param engineTweaksDir:
        :param cacheWorkflowDir:
        :param cacheWorkflowInputsDir:
        :param workDir:
        :param outputsDir:
        :param intermediateDir:
        :param tempDir:
        :param secure_exec:
        :param config_directory:
        """
        if local_config is None:
            local_config = dict()
        if workflow_config is None:
            workflow_config = dict()
        self.local_config = local_config

        if config_directory is None:
            config_directory = cast("AbsPath", os.getcwd())
        self.config_directory = config_directory

        # Getting a logger focused on specific classes
        self.logger = logging.getLogger(self.__class__.__name__)

        # This one may be needed to identify container overrides
        # or specific engine versions
        self.workflow_config = workflow_config

        # cacheDir
        if cacheDir is None:
            cacheDir = local_config.get("cacheDir")

        if cacheDir is None:
            cacheDir = cast(
                "AbsPath", tempfile.mkdtemp(prefix="WfExS", suffix="backend")
            )
            # Assuring this temporal directory is removed at the end
            atexit.register(shutil.rmtree, cacheDir, True)
        else:
            if not os.path.isabs(cacheDir):
                cacheDir = cast(
                    "AbsPath",
                    os.path.normpath(os.path.join(config_directory, cacheDir)),
                )
            # Be sure the directory exists
            os.makedirs(cacheDir, exist_ok=True)

        # We are using as our own caching directory one located at the
        # generic caching directory, with the name of the class
        # This directory will hold software installations, for instance
        self.weCacheDir = os.path.join(cacheDir, self.__class__.__name__)

        # Needed for those cases where alternate version of the workflow is generated
        if cacheWorkflowDir is None:
            cacheWorkflowDir = cast("AbsPath", os.path.join(cacheDir, "wf-cache"))
            os.makedirs(cacheWorkflowDir, exist_ok=True)
        self.cacheWorkflowDir = cacheWorkflowDir

        # Needed for those cases where there is a shared cache
        if cacheWorkflowInputsDir is None:
            cacheWorkflowInputsDir = cast(
                "AbsPath", os.path.join(cacheDir, "wf-inputs")
            )
            os.makedirs(cacheWorkflowInputsDir, exist_ok=True)
        self.cacheWorkflowInputsDir = cacheWorkflowInputsDir

        # Setting up working directories, one per instance
        if workDir is None:
            workDir = cast(
                "AbsPath", tempfile.mkdtemp(prefix="WfExS-exec", suffix="workdir")
            )
            # Assuring this temporal directory is removed at the end
            atexit.register(shutil.rmtree, workDir, True)
        self.workDir = pathlib.Path(workDir)

        # This directory should hold intermediate workflow steps results
        if intermediateDir is None:
            self.intermediateDir = self.workDir / WORKDIR_INTERMEDIATE_RELDIR
        else:
            self.intermediateDir = pathlib.Path(intermediateDir)
        self.intermediateDir.mkdir(parents=True, exist_ok=True)

        # This directory will hold the final workflow results, which could
        # be either symbolic links to the intermediate results directory
        # or newly generated content
        if outputsDir is None:
            self.outputsDir = self.workDir / WORKDIR_OUTPUTS_RELDIR
        else:
            self.outputsDir = pathlib.Path(outputsDir)

        if not self.outputsDir.is_absolute():
            self.outputsDir = self.outputsDir.absolute()

        self.outputsDir.mkdir(parents=True, exist_ok=True)

        # This directory will hold diverse metadata, like execution metadata
        # or newly generated content
        if outputMetaDir is None:
            self.outputMetaDir = (
                self.workDir / WORKDIR_META_RELDIR / WORKDIR_OUTPUTS_RELDIR
            )
        else:
            self.outputMetaDir = pathlib.Path(outputMetaDir)

        self.outputMetaDir.mkdir(parents=True, exist_ok=True)

        # This directory will hold stats metadata, as well as the dot representation
        # of the workflow execution
        outputStatsDir = self.outputMetaDir / WORKDIR_STATS_RELDIR
        outputStatsDir.mkdir(parents=True, exist_ok=True)
        self.outputStatsDir = outputStatsDir

        # This directory is here for those files which are created in order
        # to tweak or patch workflow executions
        # engine tweaks directory
        if engineTweaksDir is None:
            self.engineTweaksDir = self.workDir / WORKDIR_ENGINE_TWEAKS_RELDIR
        else:
            self.engineTweaksDir = pathlib.Path(engineTweaksDir)
        self.engineTweaksDir.mkdir(parents=True, exist_ok=True)

        # This directory is here for temporary files of any program launched from
        # WfExS or the engine itself. It should be set to TMPDIR on subprocess calls
        if tempDir is None:
            tempDir = cast(
                "AbsPath", tempfile.mkdtemp(prefix="WfExS-exec", suffix="tempdir")
            )
            # Assuring this temporal directory is removed at the end
            atexit.register(shutil.rmtree, tempDir, True)
        self.tempDir = tempDir

        # This directory will hold the staged containers to be used
        if stagedContainersDir is None:
            stagedContainersDir = cast(
                "AbsPath", os.path.join(workDir, WORKDIR_CONTAINERS_RELDIR)
            )
        elif not os.path.isabs(stagedContainersDir):
            stagedContainersDir = cast("AbsPath", os.path.abspath(stagedContainersDir))
        os.makedirs(stagedContainersDir, exist_ok=True)
        self.stagedContainersDir = pathlib.Path(stagedContainersDir)

        # Setting up common properties
        tools_config = local_config.get("tools", {})
        self.docker_cmd = tools_config.get("dockerCommand", DEFAULT_DOCKER_CMD)
        engine_mode = tools_config.get("engineMode")
        if engine_mode is None:
            engine_mode = DEFAULT_ENGINE_MODE
        else:
            engine_mode = EngineMode(engine_mode)
        self.engine_mode = engine_mode

        container_type = container_factory_clazz.ContainerType()
        if not self.SupportsContainerType(container_type):
            raise WorkflowEngineException(
                f"Current implementation of {self.__class__.__name__} does not support {container_type}"
            )

        if secure_exec and not self.SupportsSecureExecContainerType(container_type):
            raise WorkflowEngineException(
                f"Due technical limitations, secure or paranoid executions are incompatible with {container_type}"
            )

        self.logger.debug(f"Instantiating container type {container_type}")
        # For materialized containers, we should use common directories
        # This for the containers themselves
        containersCacheDir = (
            pathlib.Path(cacheDir) / "containers" / container_factory_clazz.__name__
        )
        self.container_factory = container_factory_clazz(
            simpleFileNameMethod=self.simpleContainerFileName,
            containersCacheDir=containersCacheDir,
            stagedContainersDir=self.stagedContainersDir,
            tools_config=tools_config,
            engine_name=self.__class__.__name__,
            tempDir=pathlib.Path(self.tempDir),
        )

        isUserNS = self.container_factory.supportsFeature("userns")
        self.logger.debug(
            f"Flags: secure => {secure_exec} , userns => {isUserNS} , allowOther => {allowOther}"
        )
        if (
            self.container_factory.containerType == ContainerType.Singularity
            and secure_exec
        ):
            if not allowOther and not isUserNS:
                self.logger.error(
                    f"Secure executions do not work without either enabling FUSE use_allow_other in /etc/fuse.conf or userns in {container_type} system installation"
                )

            if not isUserNS:
                self.logger.error(
                    f"Paranoid executions do not work without enabling userns in {container_type} system installation"
                )

        # Locating the payloads directory, where the nodejs wrapper should be placed
        self.payloadsDir = os.path.join(os.path.dirname(__file__), "payloads")

        # Whether the containers of each step are writable
        self.writable_containers = workflow_config.get("writable_containers", False)

        if (
            secure_exec
            and self.writable_containers
            and self.container_factory.ContainerType() == ContainerType.Singularity
        ):
            raise WorkflowEngineException(
                "FATAL: secure execution and writable containers are incompatible when singularity is being used"
            )

        self.secure_exec = secure_exec

    @classmethod
    def FromStagedSetup(
        cls,
        staged_setup: "StagedSetup",
        container_factory_classes: "Sequence[Type[ContainerFactory]]" = [
            NoContainerFactory
        ],
        cache_dir: "Optional[AnyPath]" = None,
        cache_workflow_dir: "Optional[AnyPath]" = None,
        cache_workflow_inputs_dir: "Optional[AnyPath]" = None,
        local_config: "Optional[EngineLocalConfig]" = None,
        config_directory: "Optional[AnyPath]" = None,
    ) -> "AbstractWorkflowEngineType":
        """
        Init method from staged setup instance

        :param staged_setup:
        :param cache_dir:
        :param cache_workflow_dir:
        :param cache_workflow_inputs_dir:
        :param local_config:
        :param config_directory:
        """

        the_container_factory_clazz: "Optional[Type[ContainerFactory]]" = None
        for container_factory_clazz in container_factory_classes:
            if container_factory_clazz.ContainerType() == staged_setup.container_type:
                the_container_factory_clazz = container_factory_clazz
                # self.logger.debug(f"Selected container type {staged_setup.container_type}")
                break
        else:
            raise WorkflowEngineException(
                f"FATAL: No container factory implementation for {staged_setup.container_type}"
            )
        return cls(
            container_factory_clazz=the_container_factory_clazz,
            workflow_config=staged_setup.workflow_config,
            engineTweaksDir=staged_setup.engine_tweaks_dir,
            workDir=staged_setup.work_dir,
            outputsDir=staged_setup.outputs_dir,
            intermediateDir=staged_setup.intermediate_dir,
            tempDir=staged_setup.temp_dir,
            secure_exec=staged_setup.secure_exec,
            allowOther=staged_setup.allow_other,
            cacheDir=cache_dir,
            cacheWorkflowDir=cache_workflow_dir,
            cacheWorkflowInputsDir=cache_workflow_inputs_dir,
            stagedContainersDir=staged_setup.containers_dir,
            local_config=local_config,
            config_directory=config_directory,
        )

    def getConfiguredContainerType(self) -> "ContainerType":
        return self.container_factory.containerType

    @classmethod
    @abc.abstractmethod
    def SupportedContainerTypes(cls) -> "Set[ContainerType]":
        pass

    @classmethod
    @abc.abstractmethod
    def SupportedSecureExecContainerTypes(cls) -> "Set[ContainerType]":
        pass

    @classmethod
    def SupportsContainerType(cls, container_type: "ContainerType") -> "bool":
        return container_type in cls.SupportedContainerTypes()

    @classmethod
    def SupportsContainerFactory(
        cls, container_factory_clazz: "Type[ContainerFactory]"
    ) -> "bool":
        return cls.SupportsContainerType(container_factory_clazz.ContainerType())

    @classmethod
    def SupportsSecureExecContainerType(cls, container_type: "ContainerType") -> "bool":
        return container_type in cls.SupportedSecureExecContainerTypes()

    @classmethod
    def SupportsSecureExecContainerFactory(
        cls, container_factory_clazz: "Type[ContainerFactory]"
    ) -> "bool":
        return cls.SupportsSecureExecContainerType(
            container_factory_clazz.ContainerType()
        )

    @abc.abstractmethod
    def identifyWorkflow(
        self, localWf: "LocalWorkflow", engineVer: "Optional[EngineVersion]" = None
    ) -> "Union[Tuple[EngineVersion, LocalWorkflow], Tuple[None, None]]":
        """
        This method should return the effective engine version needed
        to run it when this workflow engine recognizes the workflow type
        """
        pass

    @abc.abstractmethod
    def materializeEngineVersion(
        self, engineVersion: "EngineVersion"
    ) -> "Tuple[EngineVersion, EnginePath, Fingerprint]":
        """
        Method to ensure the required engine version is materialized
        It should raise an exception when the exact version is unavailable,
        and no replacement could be fetched
        """

        pass

    @staticmethod
    def GetEngineVersion(
        matWfEng: "MaterializedWorkflowEngine",
    ) -> "WorkflowEngineVersionStr":
        """
        It must return a string in the form of
        "{symbolic engine name} {version}"
        """
        return matWfEng.instance._get_engine_version_str(matWfEng)

    def materializeEngine(
        self,
        localWf: "LocalWorkflow",
        engineVersion: "Optional[EngineVersion]" = None,
        do_identify: "bool" = False,
    ) -> "Optional[MaterializedWorkflowEngine]":
        """
        Method to ensure the required engine version is materialized
        It should raise an exception when the exact version is unavailable,
        and no replacement could be fetched
        """

        # This method can be forced to materialize an specific engine version
        if do_identify or engineVersion is None:
            # The identification could return an augmented LocalWorkflow instance
            resLocalWf: "Optional[LocalWorkflow]"
            engineVersion, resLocalWf = self.identifyWorkflow(localWf, engineVersion)
            if engineVersion is None:
                return None
            else:
                assert resLocalWf is not None
                localWf = resLocalWf

        # This is needed for those cases where there is no exact match
        # on the available engine version
        engineVersion, enginePath, engineFingerprint = self.materializeEngineVersion(
            engineVersion
        )

        return MaterializedWorkflowEngine(
            instance=self,
            version=engineVersion,
            fingerprint=engineFingerprint,
            engine_path=enginePath,
            workflow=localWf,
        )

    @abc.abstractmethod
    def materializeWorkflow(
        self,
        matWorfklowEngine: "MaterializedWorkflowEngine",
        consolidatedWorkflowDir: "AbsPath",
        offline: "bool" = False,
    ) -> "Tuple[MaterializedWorkflowEngine, Sequence[ContainerTaggedName]]":
        """
        Method to ensure the workflow has been materialized. It returns the
        localWorkflow directory, as well as the list of containers

        For Nextflow it is usually a no-op, but for CWL it requires resolution
        """

        pass

    def sideContainers(self) -> "Sequence[ContainerTaggedName]":
        """
        Containers needed by the engine to work
        """
        return list()

    @abc.abstractmethod
    def simpleContainerFileName(self, imageUrl: "URIType") -> "RelPath":
        """
        This method must be implemented to tell which names expect the workflow engine
        on its container cache directories when an image is locally materialized
        (currently only useful for Singularity)
        """
        pass

    def materialize_containers(
        self,
        listOfContainerTags: "Sequence[ContainerTaggedName]",
        containersDir: "Optional[AnyPath]" = None,
        offline: "bool" = False,
        force: "bool" = False,
        injectable_containers: "Sequence[Container]" = [],
    ) -> "Tuple[ContainerEngineVersionStr, Sequence[Container], ContainerOperatingSystem, ProcessorArchitecture]":
        if containersDir is None:
            containersDirPath = self.stagedContainersDir
        else:
            containersDirPath = pathlib.Path(containersDir)

        return (
            self.container_factory.engine_version(),
            self.container_factory.materializeContainers(
                listOfContainerTags,
                containers_dir=containersDirPath,
                offline=offline,
                force=force,
                injectable_containers=injectable_containers,
            ),
            *self.container_factory.architecture,
        )

    def deploy_containers(
        self,
        containers_list: "Sequence[Container]",
        containersDir: "Optional[AnyPath]" = None,
        force: "bool" = False,
    ) -> "Sequence[Container]":
        if containersDir is None:
            containersDirPath = self.stagedContainersDir
        else:
            containersDirPath = pathlib.Path(containersDir)

        return self.container_factory.deployContainers(
            containers_list=containers_list,
            containers_dir=containersDirPath,
            force=force,
        )

    @property
    def staged_containers_dir(self) -> "AnyPath":
        return cast("AbsPath", self.stagedContainersDir.as_posix())

    def create_job_directories(
        self,
    ) -> "Tuple[str, pathlib.Path, pathlib.Path, pathlib.Path]":
        outputDirPostfix = "_" + str(int(time.time())) + "_" + str(os.getpid())
        intermediateDir = self.intermediateDir / outputDirPostfix
        intermediateDir.mkdir(parents=True, exist_ok=True)
        outputsDir = self.outputsDir / outputDirPostfix
        outputsDir.mkdir(parents=True, exist_ok=True)
        outputMetaDir = self.outputMetaDir / outputDirPostfix
        outputMetaDir.mkdir(parents=True, exist_ok=True)

        return outputDirPostfix, intermediateDir, outputsDir, outputMetaDir

    @abc.abstractmethod
    def launchWorkflow(
        self,
        matWfEng: "MaterializedWorkflowEngine",
        inputs: "Sequence[MaterializedInput]",
        environment: "Sequence[MaterializedInput]",
        outputs: "Sequence[ExpectedOutput]",
    ) -> "StagedExecution":
        pass

    @classmethod
    def ExecuteWorkflow(
        cls,
        matWfEng: "MaterializedWorkflowEngine",
        inputs: "Sequence[MaterializedInput]",
        environment: "Sequence[MaterializedInput]",
        outputs: "Sequence[ExpectedOutput]",
    ) -> "StagedExecution":
        # Now, deploy the containers to the local registry (needed for Docker)
        if matWfEng.containers is not None:
            matWfEng.instance.deploy_containers(
                matWfEng.containers, matWfEng.instance.staged_containers_dir
            )
        if matWfEng.operational_containers is not None:
            matWfEng.instance.deploy_containers(
                matWfEng.operational_containers, matWfEng.instance.staged_containers_dir
            )

        # And once deployed, let's run the workflow!
        stagedExec = matWfEng.instance.launchWorkflow(
            matWfEng, inputs, environment, outputs
        )

        return stagedExec

    @classmethod
    def MaterializeWorkflowAndContainers(
        cls,
        matWfEng: "MaterializedWorkflowEngine",
        containersDir: "AbsPath",
        consolidatedWorkflowDir: "AbsPath",
        offline: "bool" = False,
        injectable_containers: "Sequence[Container]" = [],
        injectable_operational_containers: "Sequence[Container]" = [],
    ) -> "Tuple[MaterializedWorkflowEngine, ContainerEngineVersionStr, ContainerOperatingSystem, ProcessorArchitecture]":
        matWfEngV2, listOfContainerTags = matWfEng.instance.materializeWorkflow(
            matWfEng, consolidatedWorkflowDir, offline=offline
        )

        (
            containerEngineStr,
            listOfContainers,
            containerEngineOs,
            arch,
        ) = matWfEngV2.instance.materialize_containers(
            listOfContainerTags,
            containersDir,
            offline=offline,
            injectable_containers=injectable_containers,
        )

        # Next ones are needed by the workflow engine itself
        listOfOperationalContainerTags = matWfEng.instance.sideContainers()
        if len(listOfOperationalContainerTags) > 0:
            try:
                (
                    _,
                    listOfOperationalContainers,
                    _,
                    _,
                ) = matWfEngV2.instance.materialize_containers(
                    listOfOperationalContainerTags,
                    containersDir,
                    offline=offline,
                    injectable_containers=injectable_operational_containers,
                )
            except:
                logging.debug("FIXME materializing containers")
                listOfOperationalContainers = []
        else:
            listOfOperationalContainers = []

        matWfEngV3 = MaterializedWorkflowEngine(
            instance=matWfEngV2.instance,
            version=matWfEngV2.version,
            fingerprint=matWfEngV2.fingerprint,
            engine_path=matWfEngV2.engine_path,
            workflow=matWfEngV2.workflow,
            containers_path=containersDir,
            containers=listOfContainers,
            operational_containers=listOfOperationalContainers,
        )

        return matWfEngV3, containerEngineStr, containerEngineOs, arch

    GuessedCardinalityMapping = {
        False: (0, 1),
        True: (0, sys.maxsize),
    }

    GuessedOutputKindMapping: "Mapping[str, ContentKind]" = {
        GeneratedDirectoryContent.__name__: ContentKind.Directory,
        GeneratedContent.__name__: ContentKind.File,
    }

    def identifyMaterializedOutputs(
        self,
        matInputs: "Sequence[MaterializedInput]",
        expectedOutputs: "Sequence[ExpectedOutput]",
        outputsDir: "pathlib.Path",
        outputsMapping: "Optional[Mapping[SymbolicOutputName, Any]]" = None,
    ) -> "Sequence[MaterializedOutput]":
        """
        This method is used to identify outputs by either file glob descriptions
        or matching with a mapping
        """
        if not isinstance(outputsMapping, dict):
            outputsMapping = {}

        matInputHash: "Mapping[SymbolicParamName, Union[Sequence[bool], Sequence[str], Sequence[int], Sequence[float], Sequence[MaterializedContent]]]" = {
            matInput.name: matInput.values for matInput in matInputs
        }

        matOutputs = []
        # This is only applied when no outputs sections is specified
        if len(expectedOutputs) == 0:
            if len(outputsMapping) == 0:
                # Engines like Nextflow
                iEntry = 0
                for entry in os.scandir(outputsDir):
                    matValuesDef: "Optional[MutableSequence[AbstractGeneratedContent]]" = (
                        None
                    )
                    guessedOutputKindDef: "ContentKind"
                    # We are avoiding to enter in loops around '.' and '..'
                    if entry.is_file():
                        entry_path = pathlib.Path(entry.path)
                        matValuesDef = [
                            GeneratedContent(
                                local=entry_path,
                                signature=cast(
                                    "Fingerprint",
                                    ComputeDigestFromFile(
                                        entry_path,
                                        repMethod=nihDigester,
                                    ),
                                ),
                            )
                        ]
                        guessedOutputKindDef = ContentKind.File
                    elif entry.is_dir(follow_symlinks=False):
                        matValuesDef = [
                            GetGeneratedDirectoryContent(
                                entry_path, signatureMethod=nihDigester
                            )
                        ]
                        guessedOutputKindDef = ContentKind.Directory

                    if matValuesDef is not None:
                        outputName = "unnamed_output_{}".format(iEntry)
                        iEntry += 1
                        matOutput = MaterializedOutput(
                            name=cast("SymbolicOutputName", outputName),
                            kind=guessedOutputKindDef,
                            expectedCardinality=self.GuessedCardinalityMapping[False],
                            values=matValuesDef,
                            syntheticOutput=True,
                        )

                        matOutputs.append(matOutput)
            else:
                # Engines like CWL
                for outputName, outputVal in outputsMapping.items():
                    matValues: "Sequence[AbstractGeneratedContent]" = CWLDesc2Content(
                        outputVal, self.logger, doGenerateSignatures=True
                    )

                    matValueClassName = matValues[0].__class__.__name__
                    guessedOutputKind = self.GuessedOutputKindMapping.get(
                        matValueClassName
                    )

                    if guessedOutputKind is None:
                        self.logger.error(
                            f"FIXME: Define mapping for {matValueClassName}, needed by {outputName}. Known ones are {list(self.GuessedOutputKindMapping.keys())}"
                        )
                    else:
                        matOutput = MaterializedOutput(
                            name=outputName,
                            kind=guessedOutputKind,
                            expectedCardinality=self.GuessedCardinalityMapping[
                                len(matValues) > 1
                            ],
                            values=matValues,
                            syntheticOutput=False,
                        )

                        matOutputs.append(matOutput)

        # This is only applied when the expected outputs is specified
        for expectedOutput in expectedOutputs:
            cannotBeEmpty = expectedOutput.cardinality[0] != 0
            expMatContents = cast("MutableSequence[AbstractGeneratedContent]", [])
            expMatValues = cast("MutableSequence[str]", [])
            if expectedOutput.fillFrom is not None:
                matInputValues = matInputHash.get(expectedOutput.fillFrom)
                if matInputValues is not None:
                    for matchedPath in matInputValues:
                        # FIXME: Are these elements always paths??????
                        if isinstance(matchedPath, str):
                            if os.path.isabs(matchedPath):
                                abs_matched_path = pathlib.Path(matchedPath)
                            else:
                                abs_matched_path = outputsDir / matchedPath
                            try:
                                theContent: "AbstractGeneratedContent"
                                if expectedOutput.kind == ContentKind.Directory:
                                    theContent = GetGeneratedDirectoryContent(
                                        thePath=abs_matched_path,
                                        uri=None,  # TODO: generate URIs when it is advised
                                        preferredFilename=expectedOutput.preferredFilename,
                                        signatureMethod=nihDigester,
                                    )
                                    expMatContents.append(theContent)
                                elif expectedOutput.kind == ContentKind.File:
                                    theContent = GeneratedContent(
                                        local=abs_matched_path,
                                        uri=None,  # TODO: generate URIs when it is advised
                                        signature=cast(
                                            "Fingerprint",
                                            ComputeDigestFromFile(
                                                abs_matched_path, repMethod=nihDigester
                                            ),
                                        ),
                                        preferredFilename=expectedOutput.preferredFilename,
                                    )
                                    expMatContents.append(theContent)
                                    self.logger.debug(
                                        f"Filled From {expectedOutput.preferredFilename} {matchedPath}"
                                    )
                                else:
                                    # Reading the value from a file, as the glob is telling that
                                    with abs_matched_path.open(
                                        mode="r",
                                        encoding="utf-8",
                                        errors="ignore",
                                    ) as mP:
                                        theValue = mP.read()
                                        expMatValues.append(theValue)
                            except Exception as e:
                                self.logger.exception(
                                    f"Unable to read path {abs_matched_path} ({matchedPath}) from filled input {expectedOutput.fillFrom}"
                                )
                        else:
                            self.logger.exception("FIXME!!!!!!!!!!!!")
                            raise WorkflowEngineException("FIXME!!!!!!!!!!!!")

                if (
                    len(expMatValues) == 0
                    and len(expMatContents) == 0
                    and cannotBeEmpty
                ):
                    self.logger.warning(
                        f"Output {expectedOutput.name} got no path from filled input {expectedOutput.fillFrom}"
                    )
            elif expectedOutput.glob is not None:
                filterMethod: "Callable[[Union[Union[str, bytes, os.PathLike[str], os.PathLike[bytes]], int]], bool]"
                if expectedOutput.kind == ContentKind.Directory:
                    filterMethod = os.path.isdir
                else:
                    filterMethod = os.path.isfile
                matchedPaths: "MutableSequence[AbsPath]" = []

                for matchingPath in glob.iglob(
                    os.path.join(outputsDir, expectedOutput.glob), recursive=True
                ):
                    # Getting what it is only interesting for this
                    if filterMethod(matchingPath):
                        matchedPaths.append(cast("AbsPath", matchingPath))

                if len(matchedPaths) == 0 and cannotBeEmpty:
                    self.logger.warning(
                        "Output {} got no path for pattern {}".format(
                            expectedOutput.name, expectedOutput.glob
                        )
                    )

                for matchedPath in matchedPaths:
                    matchedContent: "AbstractGeneratedContent"
                    if expectedOutput.kind == ContentKind.Directory:
                        matchedContent = GetGeneratedDirectoryContent(
                            matchedPath,
                            uri=None,  # TODO: generate URIs when it is advised
                            preferredFilename=expectedOutput.preferredFilename,
                            signatureMethod=nihDigester,
                        )
                        expMatContents.append(matchedContent)
                    elif expectedOutput.kind == ContentKind.File:
                        matchedContent = GeneratedContent(
                            local=pathlib.Path(matchedPath),
                            uri=None,  # TODO: generate URIs when it is advised
                            signature=cast(
                                "Fingerprint",
                                ComputeDigestFromFile(
                                    matchedPath, repMethod=nihDigester
                                ),
                            ),
                            preferredFilename=expectedOutput.preferredFilename,
                        )
                        expMatContents.append(matchedContent)
                    else:
                        # Reading the value from a file, as the glob is telling that
                        with open(
                            matchedPath, mode="r", encoding="utf-8", errors="ignore"
                        ) as mP:
                            matchedValue = mP.read()
                            expMatValues.append(matchedValue)
            else:
                assert (
                    self.HasExplicitOutputs()
                ), f"Workflow engine {self.MyWorkflowType().engineName} does not support explicit outputs, but received {expectedOutput}"
                outputVal = outputsMapping.get(expectedOutput.name)

                if (outputVal is None) and cannotBeEmpty:
                    self.logger.warning(
                        "Output {} got no match from the outputs mapping".format(
                            expectedOutput.name
                        )
                    )

                expMatContents = cast(
                    "MutableSequence[AbstractGeneratedContent]",
                    CWLDesc2Content(
                        outputVal,
                        self.logger,
                        expectedOutput,
                        doGenerateSignatures=True,
                    ),
                )

            matOutput = MaterializedOutput(
                name=expectedOutput.name,
                kind=expectedOutput.kind,
                expectedCardinality=expectedOutput.cardinality,
                values=expMatContents if len(expMatContents) > 0 else expMatValues,
                syntheticOutput=expectedOutput.syntheticOutput,
                filledFrom=expectedOutput.fillFrom,
                glob=expectedOutput.glob if expectedOutput.syntheticOutput else None,
            )

            matOutputs.append(matOutput)

        return matOutputs
