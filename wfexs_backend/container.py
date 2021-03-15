#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright 2020-2021 Barcelona Supercomputing Center (BSC), Spain
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
import tempfile
import atexit
import shutil
import abc
import logging

from typing import Dict, List, Tuple
from collections import namedtuple

from .common import *


class ContainerFactoryException(Exception):
    """
    Exceptions fired by instances of ContainerFactory
    """
    pass

class ContainerFactory(abc.ABC):
    def __init__(self, cacheDir=None, local_config=None, engine_name='unset'):
        """
        Abstract init method
        
        
        """
        if local_config is None:
            local_config = dict()
        self.local_config = local_config
        
        # Getting a logger focused on specific classes
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # cacheDir 
        if cacheDir is None:
            cacheDir = local_config.get('cacheDir')
            if cacheDir:
                os.makedirs(cacheDir, exist_ok=True)
            else:
                cacheDir = tempfile.mkdtemp(prefix='wfexs', suffix='backend')
                # Assuring this temporal directory is removed at the end
                atexit.register(shutil.rmtree, cacheDir)

        # But, for materialized containers, we should use common directories
        # This for the containers themselves
        self.containersCacheDir = os.path.join(cacheDir, 'containers', self.__class__.__name__)
        # This for the symlinks to the containers, following the engine convention
        self.engineContainersSymlinkDir = os.path.join(self.containersCacheDir, engine_name)
        os.makedirs(self.engineContainersSymlinkDir, exist_ok=True)
    
    @classmethod
    @abc.abstractmethod
    def ContainerType(cls) -> ContainerType:
        pass
    
    @property
    def containerType(self) -> ContainerType:
        return self.ContainerType()
    
    @property
    def cacheDir(self) -> AbsPath:
        """
        This method returns the symlink dir instead of the cache dir
        as the entries following the naming convention of the engine
        are placed in the symlink dir
        """
        return self.engineContainersSymlinkDir
    
    @abc.abstractmethod
    def materializeContainers(self, tagList: List[ContainerTaggedName], simpleFileNameMethod: ContainerFileNamingMethod) -> List[Container]:
        """
        It is assured the containers are materialized
        """
        pass

class NoContainerFactory(ContainerFactory):
    """
        The 'no container approach', for development and local installed software
    """
    #def __init__(self, cacheDir=None, local_config=None, engine_name='unset'):
    #    super().__init__(cacheDir=cacheDir, local_config=local_config, engine_name=engine_name)
    
    @classmethod
    def ContainerType(cls) -> ContainerType:
        return ContainerType.NoContainer
    
    def materializeContainers(self, tagList: List[ContainerTaggedName], simpleFileNameMethod: ContainerFileNamingMethod) -> List[Container]:
        """
        It is assured the no-containers are materialized
        i.e. it is a no-op
        """
        
        return []