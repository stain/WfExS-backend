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

import copy
import datetime

# import http.cookiejar
import json
import logging
import os
from typing import (
    cast,
    NamedTuple,
    TYPE_CHECKING,
)
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree

from defusedxml import ElementTree

from ..common import (
    MaterializedContent,
    URIWithMetadata,
)

if TYPE_CHECKING:
    from typing import (
        Any,
        Callable,
        ClassVar,
        Dict,
        IO,
        Iterable,
        Mapping,
        MutableMapping,
        MutableSet,
        Optional,
        Sequence,
        Set,
        Tuple,
        Union,
    )

    from typing_extensions import (
        Buffer,
        Final,
        Protocol,
    )

    from _typeshed import SupportsRead

    import urllib.request

    from ..common import (
        AbsPath,
        AnyContent,
        RelPath,
        SecurityContextConfig,
        SymbolicName,
        URIType,
    )

    from ..workflow import WF

    class AuthenticatedURLOpener(Protocol):
        def __call__(
            self,
            url: Union[str, urllib.request.Request],
            data: Union[Buffer, SupportsRead[bytes], Iterable[bytes], None] = None,
            timeout: Optional[float] = None,
        ) -> Any:
            ...


from . import (
    DraftEntry,
    ExportPluginException,
)

from .abstract_token_export import (
    AbstractTokenExportPlugin,
)

from ..utils.misc import (
    get_opener_with_auth,
)

from ..utils.io_wrappers import (
    DigestIOWrapper,
    LimitedStreamIOWrapper,
    MIMETypeIOWrapper,
    MultipartEncoderIOWrapper,
    MultipartFile,
)


class DataversePublisher(AbstractTokenExportPlugin):
    """
    Class to model exporting results to Dataverse
    """

    PLUGIN_NAME: "ClassVar[SymbolicName]" = cast("SymbolicName", "dataverse")

    # Is this implementation ready?
    ENABLED: "ClassVar[bool]" = False

    ATOM_CONTENT_TYPE: "Final[str]" = "application/atom+xml"
    SWORD_APP_NAMESPACE: "Final[str]" = "http://www.w3.org/2007/app"
    SWORD_APP_PREFIX: "Final[str]" = "app"
    ATOM_NAMESPACE: "Final[str]" = "http://www.w3.org/2005/Atom"
    ATOM_PREFIX: "Final[str]" = "atom"
    SWORD_TERMS_NAMESPACE: "Final[str]" = "http://purl.org/net/sword/terms/"
    SWORD_TERMS_PREFIX: "Final[str]" = "st"

    XML_NS: "Final[Dict[str, str]]" = {
        SWORD_APP_PREFIX: SWORD_APP_NAMESPACE,
        ATOM_PREFIX: ATOM_NAMESPACE,
        SWORD_TERMS_PREFIX: SWORD_TERMS_NAMESPACE,
    }

    def __init__(
        self,
        refdir: "AbsPath",
        setup_block: "Optional[SecurityContextConfig]" = None,
        licences: "Sequence[URIType]" = [],
        orcids: "Sequence[str]" = [],
        preferred_id: "Optional[str]" = None,
    ):
        super().__init__(
            refdir=refdir,
            setup_block=setup_block,
            licences=licences,
            orcids=orcids,
            preferred_id=preferred_id,
        )

        # This is for the native API
        self.datasets_api_prefix = self.api_prefix + "datasets/"

        # This is for the SWORDv2 API
        if self.api_prefix.endswith("/api/"):
            self.sword_api_prefix = self.api_prefix[:-4]
        else:
            self.sword_api_prefix = self.api_prefix
        self.sword_api_prefix += "dvn/api/data-deposit/v1.1/swordv2/"

        self.sword_opener = self.__get_sword_opener()

        # See https://guides.dataverse.org/en/latest/api/native-api.html#submit-dataset
        self.dataverse_id = self.setup_block.get("dataverse-id", "root")

        # This call helps checking the auth token gives access to the specific dataverse_id
        dataverse_collection_url = self._get_dataverse_collection_url(self.dataverse_id)
        if dataverse_collection_url is None:
            raise ExportPluginException(
                f"Dataverse {self.dataverse_id} is not available at {self.api_prefix}. Check both existence and privileges"
            )

        self.dataverse_collection_url = dataverse_collection_url

        self.depositions_api_url = (
            self.api_prefix
            + f"dataverses/{self.dataverse_id}/datasets?doNotValidate=true"
        )

    def _gen_headers(self) -> "MutableMapping[str, str]":
        return {
            "X-Dataverse-key": self.api_token,
        }

    def __get_sword_opener(self) -> "AuthenticatedURLOpener":
        return cast(
            "AuthenticatedURLOpener",
            get_opener_with_auth(self.sword_api_prefix, self.api_token, "").open,
        )

    def _sword_get_collections(self) -> "Mapping[str, str]":
        # Let's return a mapping
        req = urllib.request.Request(
            url=self.sword_api_prefix + "service-document",
        )

        dataverses: "MutableMapping[str, str]" = dict()
        try:
            with self.sword_opener(req) as sH:
                root = ElementTree.parse(sH)
                for coll in root.findall(
                    f".//{self.SWORD_APP_PREFIX}:collection", namespaces=self.XML_NS
                ):
                    colllnk = coll.attrib.get("href")
                    if colllnk is not None:
                        colldescelem = coll.find(
                            f"./{self.ATOM_PREFIX}:title", namespaces=self.XML_NS
                        )
                        if colldescelem is not None:
                            colldesc = colldescelem.text
                            if colldesc is not None:
                                self.logger.debug(
                                    f"Found dataverse {colldesc}: {colllnk}"
                                )
                                dataverses[colllnk] = colldesc

        except:
            self.logger.exception(
                f"Unable to fetch the service document from {self.sword_api_prefix}"
            )

        return dataverses

    def _get_dataverse_collection_url(self, dataverse_id: "str") -> "Optional[str]":
        possible_dataverse_url = (
            self.sword_api_prefix
            + "collection/dataverse/"
            + urllib.parse.quote(dataverse_id, safe="")
        )
        available_dataverses = self._sword_get_collections()
        return (
            possible_dataverse_url
            if possible_dataverse_url in available_dataverses
            else None
        )

    def get_pid_metadata(self, pid: "str") -> "Optional[Mapping[str, Any]]":
        if pid.isnumeric():
            req = urllib.request.Request(
                url=self.datasets_api_prefix + pid,
                headers={"Accept": "application/json", **self._gen_headers()},
            )
            try:
                with urllib.request.urlopen(req) as bH:
                    retval = json.load(bH)
                    if isinstance(retval, dict) and retval.get("status") == "OK":
                        return cast("Mapping[str, Any]", retval.get("data", {}))
            except:
                self.logger.exception(
                    f"Unable to fetch info about {pid} Dataverse entry at {self.api_prefix}"
                )
        else:
            query = {
                "persistentId": pid,
            }

            req = urllib.request.Request(
                url=self.datasets_api_prefix
                + ":persistentId/"
                + "?"
                + urllib.parse.urlencode(query, encoding="utf-8"),
                headers={"Accept": "application/json", **self._gen_headers()},
            )
            try:
                with urllib.request.urlopen(req) as bH:
                    retval = json.load(bH)
                    if isinstance(retval, dict) and retval.get("status") == "OK":
                        return cast("Mapping[str, Any]", retval.get("data", {}))
            except:
                self.logger.exception(
                    f"Unable to fetch info about {pid} Dataverse entry at {self.api_prefix}"
                )

        return None

    def get_pid_draftentry(self, pid: "str") -> "Optional[DraftEntry]":
        """
        This method is used to obtained the metadata associated to a PID,
        in case the destination allows it.
        """

        metadata = self.get_pid_metadata(pid)

        if metadata is None:
            return None

        latest_meta = metadata.get("latestVersion", {})
        return DraftEntry(
            # These assignments could be wrong
            draft_id=str(latest_meta["datasetId"]),
            pid=latest_meta["datasetPersistentId"],
            metadata=metadata,
        )

    def _sword_book_entry(self) -> "Optional[str]":
        sword_draft_entry = f"""\
<?xml version="1.0"?>
<entry xmlns="{self.ATOM_NAMESPACE}" xmlns:dcterms="http://purl.org/dc/terms/">
   <!-- some embedded metadata -->
   <dcterms:title>Draft record created at {datetime.datetime.utcnow().isoformat()}</dcterms:title>
   <dcterms:creator>WfExS-backend ghost creator</dcterms:creator>
   <!-- Dataverse controlled vocabulary subject term -->
   <dcterms:subject>Bioinformatics</dcterms:subject>
   <dcterms:description>Empty draft record created by WfExS-backend at {datetime.datetime.utcnow().isoformat()}</dcterms:description>
   <!-- Producer with financial or admin responsibility of the data -->
   <!--
   <dcterms:contributor type="Contact">CaffeineForAll</dcterms:contributor>
   -->
</entry>
"""

        req = urllib.request.Request(
            url=self.dataverse_collection_url,
            headers={
                "Content-Type": self.ATOM_CONTENT_TYPE,
            },
            data=sword_draft_entry.encode("utf-8"),
            method="POST",
        )
        try:
            with self.sword_opener(req) as bH:
                root = ElementTree.parse(bH)
                link_elem = root.find(
                    f".//{self.ATOM_PREFIX}:id", namespaces=self.XML_NS
                )
                if link_elem is None:
                    return None

                link_id = link_elem.text
                if link_id is None:
                    return None

                doipos = link_id.find("doi:")
                if doipos < 0:
                    return None

                return link_id[doipos:]
        except urllib.error.HTTPError as he:
            errmsg = f"Could not book Dataverse entry using {self.dataverse_collection_url}. Server response: {he.read().decode('utf-8')}"
            self.logger.exception(errmsg)
            # for cookie in self._shared_cookie_jar:
            #    self.logger.error(f"Cookie: {cookie.name}, {cookie.value}")
            raise ExportPluginException(errmsg) from he

    def book_pid(
        self,
        preferred_id: "Optional[str]" = None,
        initially_required_metadata: "Optional[Mapping[str, Any]]" = None,
    ) -> "Optional[DraftEntry]":
        """
        This method is used to book a new PID,
        in case the destination allows it.

        We can even "suggest" either a new or existing PID.

        It can return both the internal PID as the future, official one.
        It also returns the associated internal metadata.

        When it returns None, it means either
        the destination does not allow booking
        pids, either temporary or permanently
        """
        """
        We are booking a new PID, in case the default
        preferred id is None or an invalid one
        """
        if preferred_id is None:
            preferred_id = self.preferred_id

        booked_entry: "Optional[DraftEntry]" = None
        fill_in_new_entry = True
        newentry_url: "Optional[str]" = None
        if preferred_id is not None:
            booked_entry = self.get_pid_draftentry(preferred_id)
            if booked_entry is not None:
                # Booked
                if booked_entry.metadata is not None:
                    fill_in_new_entry = False
                    # Submitted
                    if (
                        booked_entry.metadata.get("latestVersion", {}).get(
                            "versionState"
                        )
                        != "DRAFT"
                    ):
                        # Built the request url for new version
                        # This should force the creation of a new draft
                        updated_metadata = self.update_record_metadata(
                            booked_entry, booked_entry.metadata.get("latestVersion", {})
                        )
                        if updated_metadata is not None:
                            new_draft_id = updated_metadata.get(
                                "latestVersion", {}
                            ).get("id")
                            if new_draft_id is not None:
                                return booked_entry._replace(
                                    draft_id=new_draft_id,
                                    metadata=updated_metadata,
                                )
                        return None
                        # updated_entry = self._sword_book_revision(booked_entry)
                        #
                        # return updated_entry
                    else:
                        preferred_id = booked_entry.draft_id
                else:
                    self.logger.info(
                        f"Discarding pre-booked {preferred_id} Dataverse entry id reusage from {self.api_prefix}"
                    )
            else:
                self.logger.info(
                    f"Discarding pre-booked {preferred_id} Dataverse entry id reusage from {self.api_prefix}"
                )

        if fill_in_new_entry:
            # With the booked_id
            booked_id = self._sword_book_entry()

            # we are getting the entry in the native API
            if booked_id is not None:
                booked_entry = self.get_pid_draftentry(booked_id)
            # Book new entry
            newentry_url = self.depositions_api_url

        # TO BE FINISHED
        return booked_entry

    def discard_booked_pid(self, pid_or_draft: "Union[str, DraftEntry]") -> "bool":
        """
        This method is used to release a previously booked PID,
        which has not been published.

        When it returns False, it means that the
        provided id did exist, but it was not a draft
        """
        if isinstance(pid_or_draft, str):
            if pid_or_draft.isnumeric():
                draft_id = pid_or_draft
            else:
                pid_meta = self.get_pid_draftentry(pid_or_draft)
                if pid_meta is None:
                    return False

                draft_id = pid_meta.draft_id
        else:
            draft_id = pid_or_draft.draft_id

        req = urllib.request.Request(
            url=self.datasets_api_prefix + f"{draft_id}/versions/:draft",
            headers=self._gen_headers(),
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(req) as bH:
                retval = json.load(bH)
                return isinstance(retval, dict) and retval.get("status") == "OK"
        except:
            self.logger.exception(
                f"Unable to fetch info about {draft_id} Dataverse entry at {self.api_prefix}"
            )

        return False

    def get_file_bucket_prefix(
        self,
        draft_entry: "DraftEntry",
    ) -> "str":
        """
        This is an accessory method which is used to build upload paths
        """
        query = {
            "persistentId": draft_entry.pid,
        }

        return (
            self.datasets_api_prefix
            + ":persistentId/add"
            + "?"
            + urllib.parse.urlencode(query, encoding="utf-8")
        )

    def _direct_upload_file_to_draft(
        self,
        draft_entry: "DraftEntry",
        filename: "Union[str, IO[bytes]]",
        remote_filename: "Optional[str]",
        content_size: "Optional[int]" = None,
    ) -> "Mapping[str, Any]":
        """
        It takes as input the draft record representation, a local filename and optionally the remote filename to use
        """
        # curl -H "X-Dataverse-key:$API_TOKEN" -X POST -F "file=@$FILENAME" -F 'jsonData={"description":"My description.","directoryLabel":"data/subdir1","categories":["Data"], "restrict":"false", "tabIngest":"false"}' "$SERVER_URL/api/datasets/:persistentId/add?persistentId=$PERSISTENT_ID"

        pH: "SupportsRead[bytes]"
        if isinstance(filename, str):
            pH = open(filename, mode="rb")
            content_size = os.stat(filename).st_size
            if remote_filename is None:
                remote_filename = os.path.relpath(filename, self.refdir)
        else:
            assert isinstance(
                remote_filename, str
            ), "When filename is a data stream, remote_filename must be declared"
            assert (
                content_size is not None
            ), "When filename is a data stream, content_size must be declared"
            pH = filename

        filename_label: "str" = remote_filename
        directory_label: "Optional[str]" = None
        rslash = remote_filename.rfind("/")
        if rslash > 0:
            directory_label = remote_filename[0:rslash]
            filename_label = remote_filename[rslash + 1 :]

        # The code is guessing the MIME type of the stream on the fly
        mpH = MIMETypeIOWrapper(stream=pH)

        # Also, the code is computing the SHA256 of the stream on the fly
        dpH = DigestIOWrapper(stream=mpH, algo="sha256")

        # Assuming direct uploads are available ....
        query = {
            "persistentId": draft_entry.pid,
            "size": content_size,
        }
        req = urllib.request.Request(
            url=self.datasets_api_prefix
            + ":persistentId/uploadurls"
            + "?"
            + urllib.parse.urlencode(query, encoding="utf-8"),
            headers=self._gen_headers(),
        )
        with urllib.request.urlopen(req) as uF:
            upload_desc = json.load(uF)

            if upload_desc.get("status") != "OK":
                raise ExportPluginException()
        storage_identifier = upload_desc.get("data", {}).get("storageIdentifier")
        if not isinstance(storage_identifier, str):
            raise ExportPluginException()

        upload_url = upload_desc.get("data", {}).get("url")
        if isinstance(upload_url, str):
            putreq = urllib.request.Request(
                url=upload_url,
                data=dpH,
                method="PUT",
                headers={
                    "x-amz-tagging:dv-state": "temp",
                },
            )
            with urllib.request.urlopen(putreq) as pr:
                # upload_response = cast("Mapping[str, Any]", json.load(pr))
                pass
        else:
            upload_urls_mapping: "Optional[Mapping[str,str]]" = upload_desc.get(
                "data", {}
            ).get("urls")
            if not isinstance(upload_urls_mapping, dict):
                raise ExportPluginException()

            # Sorting the URLs in a list
            upload_urls = list(
                sorted(upload_urls_mapping.items(), key=lambda p: int(p[0]))
            )

            maxreadsize: "Optional[int]" = upload_desc.get("data", {}).get("partSize")
            if not isinstance(maxreadsize, int):
                raise ExportPluginException()

            abort_url: "Optional[str]" = upload_desc.get("data", {}).get("abort")
            if not isinstance(abort_url, str):
                raise ExportPluginException()

            completion_url: "Optional[str]" = upload_desc.get("data", {}).get(
                "complete"
            )
            if not isinstance(completion_url, str):
                raise ExportPluginException()

            try:
                etags: "MutableMapping[str, str]" = {}
                for upload_label, upload_url in upload_urls:
                    lH = LimitedStreamIOWrapper(stream=dpH, maxreadsize=maxreadsize)
                    putreq = urllib.request.Request(
                        url=upload_url,
                        data=lH,
                        method="PUT",
                        headers={
                            "x-amz-tagging:dv-state": "temp",
                        },
                    )
                    with urllib.request.urlopen(putreq) as pr:
                        etag = pr.headers.get("ETag")
                        if etag is None:
                            raise ExportPluginException()

                        etags[upload_label] = etag

                creq = urllib.request.Request(
                    url=completion_url,
                    data=json.dumps(etags).encode("utf-8"),
                    method="PUT",
                )
                with urllib.request.urlopen(creq) as cr:
                    pass
            except Exception as e:
                # In case of some failure, remove remote fragments
                dreq = urllib.request.Request(
                    url=abort_url,
                    method="DELETE",
                )
                with urllib.request.urlopen(creq) as cr:
                    pass
                raise e

        # Now, time to attach the file to the entry
        JSON_DATA = {
            "description": "My description.",
            "categories": ["Data"],
            "restrict": "false",
            "storageIdentifier": storage_identifier,
            "fileName": filename_label,
            "mimeType": mpH.mime(),
            "checksum": {
                # This must correlate with the algorithm
                # Supported ones are declared at https://guides.dataverse.org/en/latest/developers/s3-direct-upload-api.html#adding-the-uploaded-file-to-the-dataset
                "@type": "SHA-256",
                "@value": dpH.hexdigest(),
            },
        }
        if directory_label is not None:
            JSON_DATA["directoryLabel"] = directory_label

        areq = urllib.request.Request(
            url=self.get_file_bucket_prefix(draft_entry),
            data=urllib.parse.urlencode(
                {"jsonData": json.dumps(JSON_DATA)}, encoding="utf-8"
            ).encode("ascii"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                **self._gen_headers(),
            },
            method="POST",
        )
        with urllib.request.urlopen(areq) as aH:
            upload_response = cast("Mapping[str, Any]", json.load(aH))

        return upload_response

    def _form_upload_file_to_draft(
        self,
        draft_entry: "DraftEntry",
        filename: "Union[str, IO[bytes]]",
        remote_filename: "Optional[str]",
        content_size: "Optional[int]" = None,
    ) -> "Mapping[str, Any]":
        pH: "SupportsRead[bytes]"
        if isinstance(filename, str):
            pH = open(filename, mode="rb")
            content_size = os.stat(filename).st_size
            if remote_filename is None:
                remote_filename = os.path.relpath(filename, self.refdir)
        else:
            assert isinstance(
                remote_filename, str
            ), "When filename is a data stream, remote_filename must be declared"
            assert (
                content_size is not None
            ), "When filename is a data stream, content_size must be declared"
            pH = filename

        filename_label: "str" = remote_filename
        directory_label: "Optional[str]" = None
        rslash = remote_filename.rfind("/")
        if rslash > 0:
            directory_label = remote_filename[0:rslash]
            filename_label = remote_filename[rslash + 1 :]

        # Now, time to attach the file to the entry
        JSON_DATA = {
            "description": "My description.",
            "categories": ["Data"],
            "restrict": "false",
            "tabIngest": "false",
            "fileName": filename_label,
        }
        if directory_label is not None:
            JSON_DATA["directoryLabel"] = directory_label

        mpH = MIMETypeIOWrapper(pH)

        mencH = MultipartEncoderIOWrapper(
            [
                ("jsonData", [json.dumps(JSON_DATA)]),
                (
                    "file",
                    [
                        MultipartFile(
                            filename=filename_label,
                            mime=mpH.mime(),
                            stream=cast("SupportsRead[bytes]", mpH),
                            size=content_size,
                        )
                    ],
                ),
            ]
        )
        areq = urllib.request.Request(
            url=self.get_file_bucket_prefix(draft_entry),
            data=mencH,
            headers={
                "Content-Type": mencH.content_type,
                **self._gen_headers(),
            },
            method="POST",
        )
        with urllib.request.urlopen(areq) as aH:
            upload_response = cast("Mapping[str, Any]", json.load(aH))

        return upload_response

    def upload_file_to_draft(
        self,
        draft_entry: "DraftEntry",
        filename: "Union[str, IO[bytes]]",
        remote_filename: "Optional[str]",
        content_size: "Optional[int]" = None,
    ) -> "Mapping[str, Any]":
        try:
            # First, try the direct upload API
            return self._direct_upload_file_to_draft(
                draft_entry, filename, remote_filename, content_size=content_size
            )
        except urllib.error.HTTPError as he:
            if he.code != 404:
                errmsg = f"Could not upload file to Dataverse draft entry. Server response: {he.read().decode('utf-8')}"
                self.logger.exception(errmsg)
                # for cookie in self._shared_cookie_jar:
                #    self.logger.error(f"Cookie: {cookie.name}, {cookie.value}")
                raise ExportPluginException(errmsg) from he

            return self._form_upload_file_to_draft(
                draft_entry, filename, remote_filename, content_size=content_size
            )

    def __gen_sword_dataset_url(self, draft_entry: "DraftEntry") -> "str":
        return (
            self.sword_api_prefix
            + "edit/study/"
            + urllib.parse.quote(draft_entry.pid, safe="/:")
        )

    EXPORT_FORMATS: "Final[Set[str]]" = {
        "ddi",
        "oai_ddi",
        "dcterms",
        "oai_dc",
        "schema.org",
        "OAI_ORE",
        "Datacite",
        "oai_datacite",
        "dataverse_json",
    }

    def _export_metadata_raw(
        self, draft_entry: "DraftEntry", format: "str" = "dcterms"
    ) -> "bytes":
        """
        Get the metadata in SWORD format
        """
        if format not in self.EXPORT_FORMATS:
            raise KeyError(f"{format} is an unsupported export format")

        query = {
            "exporter": format,
            "persistentId": draft_entry.pid,
        }

        req = urllib.request.Request(
            url=self.datasets_api_prefix
            + "export"
            + "?"
            + urllib.parse.urlencode(query, encoding="utf-8"),
            headers=self._gen_headers(),
        )
        try:
            with urllib.request.urlopen(req) as bH:
                return cast("bytes", bH.read())
        except urllib.error.HTTPError as he:
            errmsg = f"Could not get metadata from entry {draft_entry.pid} . Server response: {he.read().decode('utf-8')}"
            self.logger.exception(errmsg)
            raise ExportPluginException(errmsg) from he

    def _sword_book_revision(self, draft_entry: "DraftEntry") -> "Optional[DraftEntry]":
        existing_metadata_raw = self._export_metadata_raw(draft_entry, format="dcterms")

        req = urllib.request.Request(
            url=self.__gen_sword_dataset_url(draft_entry),
            headers={
                "Content-Type": self.ATOM_CONTENT_TYPE,
            },
            data=existing_metadata_raw,
            method="PUT",
        )
        try:
            with self.sword_opener(req) as bH:
                root = ElementTree.parse(bH)
                link_elem = root.find(
                    f".//{self.ATOM_PREFIX}:id", namespaces=self.XML_NS
                )
                if link_elem is None:
                    return None

                link_id = link_elem.text
                if link_id is None:
                    return None

                doipos = link_id.find("doi:")
                if doipos < 0:
                    return None

                return self.get_pid_draftentry(link_id[doipos:])
        except urllib.error.HTTPError as he:
            errmsg = f"Could not book Dataverse entry using {self.dataverse_collection_url}. Server response: {he.read().decode('utf-8')}"
            self.logger.exception(errmsg)
            # for cookie in self._shared_cookie_jar:
            #    self.logger.error(f"Cookie: {cookie.name}, {cookie.value}")
            raise ExportPluginException(errmsg) from he

    def update_record_metadata(
        self,
        draft_entry: "DraftEntry",
        metadata: "Mapping[str, Any]",
        community_specific_metadata: "Optional[Mapping[str, Any]]" = None,
    ) -> "Mapping[str, Any]":
        """
        This method updates the draft record metadata,
        both the general one, and the specific of the community.
        This one could not make sense for some providers.
        For non draft entries, it fails
        """

        cleaned_metadata = cast("MutableMapping[str, Any]", copy.copy(metadata))
        for key in ("files", "versionState"):
            if key in cleaned_metadata:
                del cleaned_metadata[key]

        if metadata.get("versionState") != "DRAFT":
            cleaned_metadata["versionNumber"] += 1

        query = {
            "persistentId": draft_entry.pid,
        }

        req = urllib.request.Request(
            url=self.datasets_api_prefix
            + ":persistentId/versions/:draft"
            + "?"
            + urllib.parse.urlencode(query, encoding="utf-8"),
            headers={"Content-Type": "application/json", **self._gen_headers()},
            data=json.dumps(cleaned_metadata).encode("utf-8"),
            method="PUT",
        )
        try:
            with urllib.request.urlopen(req) as bH:
                retval = json.load(bH)
                return cast("Mapping[str, Any]", retval.get("data", {}))
        except urllib.error.HTTPError as he:
            # This corner case happens when there is a draft already
            if he.code == 400:
                err_payload = json.load(he)
                self.logger.exception(
                    f"Error {he.code} on update metadata {err_payload}"
                )
                self.logger.exception(f"See also {metadata}")
            raise ExportPluginException(
                f"Unable to update metadata about {draft_entry.pid} Dataverse entry at {self.api_prefix}"
            ) from he
        except:
            self.logger.exception(
                f"Unable to update metadata about {draft_entry.pid} Dataverse entry at {self.api_prefix}"
            )
            raise

    def publish_draft_record(
        self,
        draft_entry: "DraftEntry",
    ) -> "Mapping[str, Any]":
        """
        This method publishes a draft record, so its public id is permanent
        """
        query = {"persistentId": draft_entry.pid, "type": "major"}

        req = urllib.request.Request(
            url=self.api_prefix
            + "datasets/:persistentId/actions/:publish"
            + "?"
            + urllib.parse.urlencode(query, encoding="utf-8"),
            headers={"Accept": "application/json", **self._gen_headers()},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as bH:
                retval = json.load(bH)
                if isinstance(retval, dict) and retval.get("status") == "OK":
                    return cast("Mapping[str, Any]", retval.get("data", {}))
        except:
            self.logger.exception(
                f"Unable to fetch info about {draft_entry.pid} Dataverse entry at {self.api_prefix}"
            )

        return {}

    def push(
        self,
        items: "Sequence[AnyContent]",
        preferred_id: "Optional[str]" = None,
    ) -> "Sequence[URIWithMetadata]":
        """
        This is the method to be implemented by the stateful pusher
        """
        raise NotImplementedError()
