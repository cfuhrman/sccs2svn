#!/usr/bin/env python

import os
import shutil

""" Check to see if we can convert a very simple repository. """

if os.path.exists("auth") : shutil.rmtree("auth")
if os.path.exists("test01-svn") : shutil.rmtree("test01-svn")
os.popen("../../src/sccs2svn.py  --user admin --svn-repository test01-svn --sccs-repository . ")


