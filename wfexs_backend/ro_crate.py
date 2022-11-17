#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright 2020-2022 Barcelona Supercomputing Center (BSC), Spain
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

import copy
import logging
import os
import pathlib
from typing import (
    cast,
    TYPE_CHECKING,
    Any,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
)
import urllib.parse

import magic  # type: ignore
import rocrate.model.entity  # type:ignore
import rocrate.model.dataset  # type:ignore
import rocrate.model.computationalworkflow  # type:ignore
import rocrate.model.softwareapplication  # type:ignore
import rocrate.rocrate  # type:ignore

from .utils.digests import (
    nihDigester,
    ComputeDigestFromDirectory,
    ComputeDigestFromFile,
    hexDigest,
)
from .common import (
    AbstractGeneratedContent,
    AbstractWfExSException,
    ContainerType,
    ContentKind,
    Fingerprint,
    GeneratedContent,
    GeneratedDirectoryContent,
    MaterializedContent,
    MaterializedOutput,
    StagedExecution,
    SymbolicOutputName,
)


if TYPE_CHECKING:
    import datetime
    from typing import (
        Optional,
        Tuple,
        Union,
    )
    from .common import (
        Container,
        ContainerEngineVersionStr,
        ExpectedOutput,
        LocalWorkflow,
        MaterializedInput,
        MaterializedWorkflowEngine,
        RepoTag,
        RepoURL,
        URIType,
        WorkflowEngineVersionStr,
    )

logger = logging.getLogger()


class ROCrateGenerationException(AbstractWfExSException):
    pass


class FormalParameter(rocrate.model.entity.Entity):  # type: ignore[misc]
    def __init__(
        self,
        crate: "rocrate.rocrate.ROCrate",
        name: "str",
        additional_type: "Optional[str]" = None,
        identifier: "Optional[str]" = None,
        properties: "Optional[Mapping[str, Any]]" = None,
    ):
        fp_properties = {
            "name": name,
            "conformsTo": "https://bioschemas.org/profiles/FormalParameter/1.0-RELEASE/",
        }

        if additional_type is not None:
            fp_properties["additionalType"] = additional_type

        if properties is not None:
            fp_properties.update(properties)
        super().__init__(crate, identifier=identifier, properties=fp_properties)


class PropertyValue(rocrate.model.entity.Entity):  # type: ignore[misc]
    def __init__(
        self,
        crate: "rocrate.rocrate.ROCrate",
        name: "str",
        value: "Union[bool,str,int,float]",
        identifier: "Optional[str]" = None,
        properties: "Optional[Mapping[str, Any]]" = None,
    ):
        pv_properties = {
            "name": name,
            "value": value,
        }

        if properties is not None:
            pv_properties.update(properties)
        super().__init__(crate, identifier=identifier, properties=pv_properties)


class Action(rocrate.model.entity.Entity):  # type: ignore[misc]
    def __init__(
        self,
        crate: "rocrate.rocrate.ROCrate",
        name: "str",
        startTime: "datetime.datetime",
        endTime: "datetime.datetime",
        identifier: "Optional[str]" = None,
        properties: "Optional[Mapping[str, Any]]" = None,
    ):

        pv_properties = {
            "name": name,
            "startTime": startTime.isoformat(),
            "endTime": endTime.isoformat(),
        }

        if properties is not None:
            pv_properties.update(properties)
        super().__init__(crate, identifier=identifier, properties=pv_properties)


class CreateAction(Action):
    pass


def add_file_to_crate(
    crate: "rocrate.rocrate.ROCrate",
    the_path: "str",
    the_uri: "URIType",
    the_size: "Optional[int]" = None,
) -> "rocrate.model.file.File":
    the_file_crate = crate.add_file(
        source=the_uri,
        fetch_remote=False,
        validate_url=False,
    )
    if the_size is None:
        the_size = os.stat(the_path).st_size
    the_file_crate.append_to("contentSize", the_size, compact=True)
    the_file_crate.append_to(
        "sha256", ComputeDigestFromFile(the_path, repMethod=hexDigest), compact=True
    )
    the_file_crate.append_to(
        "encodingFormat", magic.from_file(the_path, mime=True), compact=True
    )

    return the_file_crate


def create_workflow_crate(
    repoURL: "RepoURL",
    repoTag: "RepoTag",
    localWorkflow: "LocalWorkflow",
    materializedEngine: "MaterializedWorkflowEngine",
    workflowEngineVersion: "Optional[WorkflowEngineVersionStr]",
    containerEngineVersion: "Optional[ContainerEngineVersionStr]",
) -> "rocrate.model.computationalworkflow.ComputationalWorkflow":
    if localWorkflow.relPath is not None:
        wf_local_path = os.path.join(localWorkflow.dir, localWorkflow.relPath)
    else:
        wf_local_path = localWorkflow.dir

    (wfCrate, compLang,) = materializedEngine.instance.getEmptyCrateAndComputerLanguage(
        localWorkflow.langVersion
    )

    wf_url = repoURL.replace(".git", "/") + "tree/" + repoTag
    if localWorkflow.relPath is not None:
        wf_url += "/" + os.path.dirname(localWorkflow.relPath)

    matWf = materializedEngine.workflow

    assert (
        matWf.effectiveCheckout is not None
    ), "The effective checkout should be available"

    parsed_repo_url = urllib.parse.urlparse(repoURL)
    if parsed_repo_url.netloc == "github.com":
        parsed_repo_path = parsed_repo_url.path.split("/")
        repo_name = parsed_repo_path[2]
        # TODO: should we urldecode repo_name?
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        wf_entrypoint_path = [
            "",  # Needed to prepend a slash
            parsed_repo_path[1],
            # TODO: should we urlencode repo_name?
            repo_name,
            matWf.effectiveCheckout,
        ]

        if localWorkflow.relPath is not None:
            wf_entrypoint_path.append(localWorkflow.relPath)

        wf_entrypoint_url = urllib.parse.urlunparse(
            (
                "https",
                "raw.githubusercontent.com",
                "/".join(wf_entrypoint_path),
                "",
                "",
                "",
            )
        )

    elif "gitlab" in parsed_repo_url.netloc:
        parsed_repo_path = parsed_repo_url.path.split("/")
        # FIXME: cover the case of nested groups
        repo_name = parsed_repo_path[2]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        wf_entrypoint_path = [parsed_repo_path[1], repo_name]
        if localWorkflow.relPath is not None:
            # TODO: should we urlencode repoTag?
            wf_entrypoint_path.extend(["-", "raw", repoTag, localWorkflow.relPath])

        wf_entrypoint_url = urllib.parse.urlunparse(
            (
                parsed_repo_url.scheme,
                parsed_repo_url.netloc,
                "/".join(wf_entrypoint_path),
                "",
                "",
                "",
            )
        )

    else:
        raise ROCrateGenerationException(
            "FIXME: Unsupported http(s) git repository {}".format(repoURL)
        )

    workflow_path = pathlib.Path(wf_local_path)
    wf_file = wfCrate.add_workflow(
        str(workflow_path),
        workflow_path.name,
        fetch_remote=False,
        main=True,
        lang=compLang,
        gen_cwl=False,
    )
    wf_file["url"] = wf_entrypoint_url
    wf_file["codeRepository"] = repoURL
    wf_file["version"] = materializedEngine.workflow.effectiveCheckout

    wf_file.append_to(
        "conformsTo",
        {"@id": "https://bioschemas.org/profiles/ComputationalWorkflow/1.0-RELEASE"},
    )
    if workflowEngineVersion is not None:
        wf_file["runtimePlatform"] = workflowEngineVersion

    if materializedEngine.containers is not None:
        add_containers_to_workflow(
            wf_file, materializedEngine.containers, containerEngineVersion
        )

    # if materializedEngine.operational_containers is not None:
    #    add_containers_to_workflow(wf_file, materializedEngine.operational_containers, containerEngineVersion)

    # if 'url' in wf_file.properties():
    #    wf_file['codeRepository'] = wf_file['url']

    # TODO: add extra files, like nextflow.config in the case of
    # Nextflow workflows, the diagram, an abstract CWL
    # representation of the workflow (when it is not a CWL workflow)
    # etc...
    # for file_entry in include_files:
    #    wfCrate.add_file(file_entry)
    wfCrate.isBasedOn = wf_file

    return wf_file


def add_directory_as_dataset(
    crate: "rocrate.rocrate.ROCrate",
    itemInLocalSource: "str",
    itemInURISource: "URIType",
) -> "Union[Tuple[rocrate.model.dataset.Dataset, Sequence[rocrate.model.file.File]], Tuple[None, None]]":
    if os.path.isdir(itemInLocalSource):
        the_files_crates: "MutableSequence[rocrate.model.file.File]" = []
        crate_dataset = crate.add_dataset(
            source=itemInURISource,
            fetch_remote=False,
            validate_url=False,
            # properties=file_properties,
        )

        # Now, recursively walk it
        with os.scandir(itemInLocalSource) as the_dir:
            for the_file in the_dir:
                if the_file.name[0] == ".":
                    continue
                the_uri = cast(
                    "URIType",
                    itemInURISource + "/" + urllib.parse.quote(the_file.name, safe=""),
                )
                if the_file.is_file():
                    the_file_crate = add_file_to_crate(
                        crate, the_file.path, the_uri, the_file.stat().st_size
                    )

                    crate_dataset.append_to("hasPart", the_file_crate)

                    the_files_crates.append(the_file_crate)
                elif the_file.is_dir():
                    # TODO: fix URI handling
                    the_dir_crate, the_subfiles_crates = add_directory_as_dataset(
                        crate, the_file.path, the_uri
                    )
                    if the_dir_crate is not None:
                        assert the_subfiles_crates is not None
                        crate_dataset.append_to("hasPart", the_dir_crate)
                        crate_dataset.append_to("hasPart", the_subfiles_crates)

                        the_files_crates.extend(the_subfiles_crates)

        return crate_dataset, the_files_crates

    return None, None


def addInputsResearchObject(
    wf_crate: "rocrate.model.computationalworkflow.ComputationalWorkflow",
    inputs: "Sequence[MaterializedInput]",
) -> "Sequence[rocrate.model.entity.Entity]":
    """
    Add the input's provenance data to a Research Object.

    :param crate: Research Object
    :type crate: ROCrate object
    :param inputs: List of inputs to add
    :type inputs: Sequence[MaterializedInput]
    """
    crate = wf_crate.crate
    crate_inputs = []
    for in_item in inputs:
        formal_parameter_id = (
            wf_crate.id + "#param:" + urllib.parse.quote(in_item.name, safe="")
        )
        itemInValue0 = in_item.values[0]
        additional_type: "Optional[str]" = None
        if isinstance(itemInValue0, int):
            additional_type = "Integer"
        elif isinstance(itemInValue0, str):
            additional_type = "String"
        elif isinstance(itemInValue0, bool):
            additional_type = "Boolean"
        elif isinstance(itemInValue0, float):
            additional_type = "Float"
        elif isinstance(itemInValue0, MaterializedContent):
            if itemInValue0.kind == ContentKind.File:
                additional_type = "File"
            elif itemInValue0.kind == ContentKind.Directory:
                additional_type = "Dataset"

        formal_parameter = FormalParameter(
            crate,
            name=in_item.name,
            identifier=formal_parameter_id,
            additional_type=additional_type,
        )
        crate.add(formal_parameter)
        wf_crate.append_to("input", formal_parameter)

        if additional_type in ("File", "Dataset"):
            for itemInValues in cast("Sequence[MaterializedContent]", in_item.values):
                # TODO: embed metadata_array in some way
                assert isinstance(itemInValues, MaterializedContent)
                itemInLocalSource = itemInValues.local  # local source
                itemInURISource = itemInValues.licensed_uri.uri  # uri source
                if os.path.isfile(itemInLocalSource):
                    crate_file = add_file_to_crate(
                        crate, itemInLocalSource, itemInURISource
                    )

                    crate_file.append_to("exampleOfWork", formal_parameter)
                    formal_parameter.append_to("workExample", crate_file)
                    crate_inputs.append(crate_file)

                elif os.path.isdir(itemInLocalSource):
                    crate_dataset, _ = add_directory_as_dataset(
                        crate, itemInLocalSource, itemInURISource
                    )
                    crate_dataset = crate.add_dataset(
                        source=itemInURISource,
                        fetch_remote=False,
                        validate_url=False,
                        # properties=file_properties,
                    )
                    crate_dataset.append_to("exampleOfWork", formal_parameter)
                    formal_parameter.append_to("workExample", crate_dataset)
                    crate_inputs.append(crate_dataset)

                else:
                    pass  # TODO: raise exception
        else:
            for itemInAtomicValues in cast(
                "Sequence[Union[bool,str,float,int]]", in_item.values
            ):
                assert isinstance(itemInAtomicValues, (bool, str, float, int))
                parameter_value = PropertyValue(crate, in_item.name, itemInAtomicValues)
                crate_pv = crate.add(parameter_value)
                crate_pv.append_to("exampleOfWork", formal_parameter)
                formal_parameter.append_to("workExample", crate_pv)
                crate_inputs.append(crate_pv)

        # TODO digest other types of inputs
    return crate_inputs


ContainerTypeIds = {
    ContainerType.Singularity: "https://apptainer.org/",
    ContainerType.Docker: "https://www.docker.com/",
}


def add_containers_to_workflow(
    wf_crate: "rocrate.model.computationalworkflow.ComputationalWorkflow",
    containers: "Sequence[Container]",
    containerEngineVersion: "Optional[ContainerEngineVersionStr]",
) -> None:
    if len(containers) > 0:
        crate = wf_crate.crate
        for container in containers:
            container_type = rocrate.model.softwareapplication.SoftwareApplication(
                crate, identifier=ContainerTypeIds[container.type]
            )
            container_type["name"] = container.type.value
            container_type["softwareVersion"] = containerEngineVersion
            crate_cont_type = crate.add(container_type)
            wf_crate.append_to("softwareRequirements", crate_cont_type)

            container_pid = container.taggedName
            software_container = rocrate.model.softwareapplication.SoftwareApplication(
                crate, identifier=container_pid
            )
            software_container["softwareVersion"] = container.fingerprint
            software_container["softwareRequirements"] = crate_cont_type

            crate_cont = crate.add(software_container)
            wf_crate.append_to("softwareRequirements", crate_cont)


def addExpectedOutputsResearchObject(
    wf_crate: "rocrate.model.computationalworkflow.ComputationalWorkflow",
    outputs: "Sequence[ExpectedOutput]",
) -> None:
    """
    Add the input's provenance data to a Research Object.

    :param crate: Research Object
    :type crate: ROCrate object
    :param inputs: List of inputs to add
    :type inputs: Sequence[MaterializedInput]
    """
    crate = wf_crate.crate
    for out_item in outputs:
        formal_parameter_id = (
            wf_crate.id + "#output:" + urllib.parse.quote(out_item.name, safe="")
        )
        if out_item.kind == ContentKind.File:
            additional_type = "File"
        elif out_item.kind == ContentKind.Directory:
            additional_type = "Dataset"
        else:
            additional_type = None

        formal_parameter = FormalParameter(
            crate,
            name=out_item.name,
            identifier=formal_parameter_id,
            additional_type=additional_type,
        )
        crate.add(formal_parameter)
        wf_crate.append_to("output", formal_parameter)


def addOutputsResearchObject(
    wf_crate: "rocrate.model.computationalworkflow.ComputationalWorkflow",
    outputs: "Sequence[MaterializedOutput]",
) -> "Sequence[rocrate.model.entity.Entity]":
    """
    Add the output's provenance data to a Research Object.

    :param crate: Research Object
    :type crate: ROCrate object
    :param outputs: List of outputs to add
    :type outputs: Sequence[MaterializedOutput]
    """
    crate = wf_crate.crate
    crate_outputs: "MutableSequence[rocrate.model.entity.Entity]" = []
    for out_item in outputs:
        # This can happen when there is no output, like when a workflow has failed
        if len(out_item.values) == 0:
            continue

        itemOutValue0 = out_item.values[0]
        additional_type: "Optional[str]" = None
        if isinstance(itemOutValue0, int):
            additional_type = "Integer"
        elif isinstance(itemOutValue0, str):
            additional_type = "String"
        elif isinstance(itemOutValue0, bool):
            additional_type = "Boolean"
        elif isinstance(itemOutValue0, float):
            additional_type = "Float"
        elif isinstance(itemOutValue0, MaterializedContent):
            if itemOutValue0.kind == ContentKind.File:
                additional_type = "File"
            elif itemOutValue0.kind == ContentKind.Directory:
                additional_type = "Dataset"

        if additional_type in ("File", "Dataset"):
            for itemOutValues in cast("Sequence[MaterializedContent]", out_item.values):

                assert isinstance(
                    itemOutValues, (GeneratedContent, GeneratedDirectoryContent)
                )
                itemOutSource = itemOutValues.local  # local source
                itemOutName = out_item.name
                properties: MutableMapping[str, SymbolicOutputName] = {
                    "name": itemOutName
                }
                if isinstance(itemOutValues, GeneratedDirectoryContent):  # if directory
                    if os.path.isdir(itemOutSource):
                        generatedDirectoryContentURI = ComputeDigestFromDirectory(
                            itemOutSource, repMethod=nihDigester
                        )  # generate directory digest
                        dirProperties: MutableMapping[str, Any] = dict.fromkeys(
                            ["hasPart"]
                        )
                        generatedContentList: MutableSequence[
                            Mapping[str, Fingerprint]
                        ] = []
                        generatedDirectoryContentList: MutableSequence[
                            Mapping[str, Fingerprint]
                        ] = []

                        assert itemOutValues.values is not None
                        for item in itemOutValues.values:
                            if isinstance(item, GeneratedContent):  # directory of files
                                fileID = item.signature
                                if fileID is None:
                                    fileID = cast(
                                        Fingerprint,
                                        ComputeDigestFromFile(
                                            item.local, repMethod=nihDigester
                                        ),
                                    )
                                fileProperties = {
                                    "name": itemOutName
                                    + "::/"
                                    + os.path.basename(item.local),
                                    "isPartOf": {
                                        "@id": generatedDirectoryContentURI
                                    },  # reference to directory containing the file
                                }
                                generatedContentList.append({"@id": fileID})
                                crate.add_file(
                                    source=fileID,
                                    fetch_remote=False,
                                    properties=fileProperties,
                                )

                            elif isinstance(
                                item, GeneratedDirectoryContent
                            ):  # directory of directories

                                # search recursively for other content inside directory
                                def search_new_content(
                                    content_list: Sequence[AbstractGeneratedContent],
                                ) -> Sequence[Mapping[str, Fingerprint]]:
                                    tempList: MutableSequence[
                                        Mapping[str, Fingerprint]
                                    ] = []
                                    for content in content_list:
                                        if isinstance(
                                            content, GeneratedContent
                                        ):  # file
                                            fileID = (
                                                content.signature
                                            )  # TODO: create a method to add files to RO-crate
                                            if fileID is None:
                                                fileID = cast(
                                                    Fingerprint,
                                                    ComputeDigestFromFile(
                                                        content.local,
                                                        repMethod=nihDigester,
                                                    ),
                                                )  # generate file digest
                                            fileProperties = {
                                                "name": itemOutName
                                                + "::/"
                                                + os.path.basename(content.local),
                                                "isPartOf": {
                                                    "@id": generatedDirectoryContentURI
                                                },  # reference to directory containing the file
                                            }
                                            tempList.append({"@id": fileID})
                                            crate.add_file(
                                                source=fileID,
                                                fetch_remote=False,
                                                properties=fileProperties,
                                            )

                                        elif isinstance(
                                            content, GeneratedDirectoryContent
                                        ):  # directory
                                            assert content.values is not None
                                            tempList.extend(
                                                search_new_content(content.values)
                                            )

                                    return tempList

                                assert item.values is not None
                                generatedDirectoryContentList.extend(
                                    search_new_content(item.values)
                                )

                            else:
                                pass  # TODO: raise exception

                        d_has_part = copy.copy(generatedDirectoryContentList)
                        d_has_part.extend(generatedContentList)
                        dirProperties["hasPart"] = d_has_part  # all the content
                        properties.update(dirProperties)
                        crate_dataset = crate.add_directory(
                            source=generatedDirectoryContentURI,
                            fetch_remote=False,
                            properties=properties,
                        )
                        crate_outputs.append(crate_dataset)

                    else:
                        errmsg = (
                            "ERROR: The output directory %s does not exist"
                            % itemOutSource
                        )
                        logger.error(errmsg)

                elif isinstance(itemOutValues, GeneratedContent):  # file
                    if os.path.isfile(itemOutSource):
                        fileID = itemOutValues.signature
                        if fileID is None:
                            fileID = cast(
                                Fingerprint,
                                ComputeDigestFromFile(
                                    itemOutSource, repMethod=nihDigester
                                ),
                            )
                        crate_file = crate.add_file(
                            source=fileID, fetch_remote=False, properties=properties
                        )
                        crate_outputs.append(crate_file)

                    else:
                        errmsg = (
                            "ERROR: The output file %s does not exist" % itemOutSource
                        )
                        logger.error(errmsg)

                else:
                    pass
                    # TODO digest other types of outputs

    return crate_outputs


def add_execution_to_crate(
    wf_crate: "rocrate.model.computationalworkflow.ComputationalWorkflow",
    stagedExec: "StagedExecution",
) -> None:
    # TODO: Add a new CreateAction for each stagedExec
    # as it is explained at https://www.researchobject.org/workflow-run-crate/profiles/workflow_run_crate
    crate = wf_crate.crate
    crate_action = CreateAction(
        crate, stagedExec.outputsDir, stagedExec.started, stagedExec.ended
    )
    crate.add(crate_action)
    crate_action["instrument"] = wf_crate

    crate_inputs = addInputsResearchObject(wf_crate, stagedExec.augmentedInputs)
    crate_action["object"] = crate_inputs
    crate_outputs = addOutputsResearchObject(wf_crate, stagedExec.matCheckOutputs)
    crate_action["result"] = crate_outputs
