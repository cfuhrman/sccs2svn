#!/usr/bin/env python

""" Convert SCCS repositories into a subversion repository.  This
    assumes that there are multiple SCCS repositories (directories)
    under a single directory; the structure is replicated in the
    subversion repository.

    All of the information in the SCCS repositories is preserved; we
    use the subversion repository layer so that we can use the SCCS dates
    and users (if we use the subversion client layer all of that gets set
    for us).

    After everything has been checked in to the Subversion repository,
    any files or directories that end with a "-" are removed; this 
    follows a local convention.  It can be replaced by commenting
    out the code following the "Delete any file ending in '-' " comment.

    The script has a method, isTextFilename that is used to determine
    if a file is a text file.  Text files have keyword expansion
    enabled.  You may wish to customize this for your installation.

    The script has another method, keyword substitution, that is used
    to replace some of the SCCS keyword identifiers with the Subversion
    keywords.  You may wish to customize this for your installation.

    Todo: Check the result of creating the repository by calling svnadmin create.
    Todo: Dates from different timezones may not be handled properly.

    Version .24 --- break up large filesets.
    Version .23 --- no longer uses the string module
    Version .22 --- close the popen result ourself

    Version .21 --- fixed the file patterns per Michael Greenberg.

    Version .2 --- only replace the project name once.




    Robert Allan Zeh (razeh@yahoo.com or razeh@earthlink.net)
    November 13th, 2005

"""

from svn import fs, repos, core, client, delta
import svn
import os
import time
import re
import shutil
import sys
try:
    from optparse import OptionParser
except ImportError:
    sys.stderr.write("This script requires the optparse module, "
                     + "present in Python 2.3 and later\n")
    sys.exit(1)
    

versions = []

def subversionTime(t):
    """ Converts a time tuple to what subversion expects. """
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", t)

def isTextFilename(filename):
    """ Determines if we should consider the filename to be a text file. """
    textPattern=".*pm|.*pl$|.*\.C$|.*\.h$|.*\.hpp$|.*\.inc$|.*\.c$|.*\.cpp$|.*\.java$|.*\.xml$|.*\.asm$|.*\.s$|.*\.S$|.*akefile$"
    return re.match(textPattern, filename)

def keywordSubstitution(input):
    """ Switches the input string from SCCS key words to Subversion keywords."""
    replacement = re.sub("%W%(\s+)%G%","$Id:$", input)
    replacement = re.sub("%W%", "$URL:$", replacement)
    replacement = re.sub("%G%", "$LastChangedDate:$", replacement)
    return replacement

class SCCSDelta:
    """ Represents a change in the SCCS repository. """
    def __init__(self, pathname, version, author, date, comment):
        self.pathname = pathname
        self.version = version
        self.author = author
        self.date = date
        self.comment = comment

    def __str__(self):
        return self.pathname + "\n" + self.version + "\n" + self.author \
               + "\n" + self.getDate() + "\n" + self.comment

    def match(self, otherDelta):
        """ Returns true if we should be in the same commit as the other delta """
        if self.author == otherDelta.author and \
           self.comment == otherDelta.comment and \
           self.pathname != otherDelta.pathname:
            if abs(time.mktime(self.date) - time.mktime(otherDelta.date)) < 10:
                return True

        return False

    def _getSourceSCCSDirectory(self):
        return os.path.split(self.pathname)[0]

    def getFilename(self):
        """ Returns the filename for this delta. """
        filename = os.path.basename(self.pathname)
        # strip off the leading "s."
        return filename[2:]

    def getRepositoryName(self):
        """ Return the name for the file in the SVN repository """
        name = self.pathname.replace("SCCS/s.", "", 1)
        return name.replace(SCCSDelta.rootDirectory, "", 1)

    def getDate(self):
        """ Return a subversion formatted date string for when this
        delta was done."""
        return subversionTime(self.date)

    def getDirectory(self):
        """ Returns the repository directory where this delta
        should be placed. """
        name = self.pathname.replace(SCCSDelta.rootDirectory, "", 1)
        return os.path.dirname(os.path.dirname(name))

    def getFileContents(self, getArguments=""):
        """ Returns, as a string, the contents of this delta. """
        command = "sccs -p " + self._getSourceSCCSDirectory() + \
                  " get " + getArguments + " -r" + self.version +\
                  " " + self.getFilename() + " -p 2> /dev/null "
        output = os.popen(command)
        deltaContents = output.read()
        output.close()
        return deltaContents

class SVNInterface:
    """ Our interface to subversion """

    def __init__(self, repositoryPath, pool):
        self.context = client.svn_client_ctx_t()
        configDirectory = core.svn_config_ensure( '', pool) 
        self.context.config = core.svn_config_get_config(configDirectory, pool)
        self.pool = pool
        self.repos_ptr = repos.svn_repos_open(repositoryPath, pool)
        self.fsob = repos.svn_repos_fs(self.repos_ptr)
        # Directories we've added to the repository.
        self.addedDirectories = {}
        self.addedDirectories["/"] = 1
        
    def _commit(self, rev, date, txn, subpool):
        """ Commit the supplied transaction to Subversion. """
        fs.change_rev_prop(self.fsob, rev,
                           core.SVN_PROP_ENTRY_COMMITTED_DATE,
                           date, subpool)

        fs.change_rev_prop(self.fsob, rev,
                           core.SVN_PROP_REVISION_DATE,
                           date, subpool)

        return repos.svn_repos_fs_commit_txn(self.repos_ptr, txn, subpool)

    def _revisionSetup(self, subpool, author, message):
        """ All of the setup for performing a revision. """
        revision = fs.youngest_rev(self.fsob, subpool)
        transaction = repos.svn_repos_fs_begin_txn_for_commit(self.repos_ptr,
                                                              revision, 
                                                              author, message,
                                                              subpool)
        root = fs.txn_root(transaction, subpool)
        return (revision, transaction, root)

    def _directoriesToAdd(self, delta):
        """ Return a list of directories to add for delta. """
        directoryName = delta.getDirectory()
        directoriesToAdd = []
        while directoryName and directoryName != "/":
            if not directoryName in self.addedDirectories:
                directoriesToAdd.insert(0, directoryName)
            directoryName = os.path.dirname(directoryName)
        return directoriesToAdd

    def _addDirectories(self, delta):
        """ Make sure that all of the directories needed for delta are added. """
        directoriesToAdd = self._directoriesToAdd(delta)
        if len(directoriesToAdd) == 0:
            return
        subpool = core.svn_pool_create(self.pool)
        (revision, transaction, root) = \
                   self._revisionSetup(subpool,
                                       options.userid,
                                       "Automatic directory addition")
        for directory in directoriesToAdd:
            print "adding directory", directory 
            print delta.getDate()
            fs.make_dir(root, directory, subpool)
            self.addedDirectories[directory] = 1

        self._commit(revision, delta.getDate(), transaction, subpool)
        core.svn_pool_destroy(subpool)
            
    def add(self, deltas):
        """ Add the supplied set of deltas to the repository.  They will
        all be added with the same user name, date, and comment. """

        # Split up the deltas array into smaller sub-arrays, otherwise
        # we choke running out of memory due to really large changesets
        # like the CDDL 2005/06/08 putback in ON that touched every file
        while len(deltas):
	    if len(deltas) > 1000:
                print "partitioning deltas into smaller new_deltas"
            new_deltas = deltas[:1000]
	    deltas = deltas[1000:]

            # Add all of the directories first, or we will be trying
            # to cross transactions, which is bad.
            for delta in new_deltas:
                self._addDirectories(delta)

            subpool = core.svn_pool_create(self.pool)
            (revision, transaction, root) = self._revisionSetup(subpool,
                                                      new_deltas[0].author,
                                                      new_deltas[0].comment)

            for delta in new_deltas:
                subversionPath = delta.getRepositoryName()
                kind = fs.check_path(root, subversionPath, subpool)
                if kind == core.svn_node_none:
                    fs.make_file(root, subversionPath, subpool)
                elif kind == core.svn_node_dir:
                    raise EnvironmentError(subversionPath +
                                           " already present as a directory.")
                handler, baton = fs.apply_textdelta(root, subversionPath,
                                                    None, None, subpool)
                svn.delta.svn_txdelta_send_string(delta.getFileContents(),
                                                  handler, baton, subpool)
                print "sending ", subversionPath, delta.getDate()

            print "committing version ",
            print self._commit(revision, delta.getDate(), transaction, subpool)
            core.svn_pool_destroy(subpool)

    def remove(self, filenames):
        """ Remove the supplied filenames file from the repository. """
        subpool = core.svn_pool_create(self.pool)
        (revision,
         transaction,
         root) = self._revisionSetup(subpool,
                                     options.userid,
                                     "Automated SCCS conversion removal")
        for file in filenames:
            print "removing ", file
            fs.delete(root, file, subpool)
        self._commit(revision, subversionTime(time.localtime()),
                     transaction, subpool)

        core.svn_pool_destroy(subpool)
        
    def propertyUpdate(self, filenames):
        """ Set the keywords property for the supplied filenames. """
        # Split up the filenames array into smaller sub-arrays, otherwise
        # we choke running out of memory due to a really large SCCS
	# repository like ON
        while len(filenames):
	    if len(filenames) > 3000:
                print "partitioning filenames into smaller new_filenames"
            new_filenames = filenames[:3000]
	    filenames = filenames[3000:]

            """ Set the keywords property for the supplied filenames. """
            subpool = core.svn_pool_create(self.pool)
            (revision,
             transaction,
             root) = self._revisionSetup(subpool,
                                     options.userid,
                                     "Automated property set")
            for filename in new_filenames:
                if isTextFilename(filename):
                    print "property set for ", filename
                    fs.change_node_prop(root, filename,
                                        core.SVN_PROP_KEYWORDS,
                                        "LastChangedDate LastChangedRevision LastChangedBy HeadURL Id",
                                        subpool)
                    fs.change_node_prop(root, filename,
                                        core.SVN_PROP_EOL_STYLE,
                                        "native",
                                        subpool)
                else:
                    print "skipping property set for ", filename
                
            self._commit(revision, subversionTime(time.localtime()),
                                 transaction, subpool)
            core.svn_pool_destroy(subpool)

    def idKeyUpdate(self, deltas):
        """ Convert the SCCS keywords inside of the supplied deltas to
        subversion keywords. """
        # Split up the deltas array into smaller sub-arrays, otherwise
        # we choke running out of memory due to really large changesets
        # like the CDDL 2005/06/08 putback in ON that touched every file
        while len(deltas):
	    if len(deltas) > 1000:
                print "partitioning deltas into smaller new_deltas"
            new_deltas = deltas[:1000]
	    deltas = deltas[1000:]

            """ Convert the SCCS keywords inside of the supplied deltas to
            subversion keywords. """
            subpool = core.svn_pool_create(self.pool)
            (revision,
             transaction,
             root) = self._revisionSetup(subpool,
                                         options.userid,
                                         "Automated keyword replacement")
            for delta in new_deltas:
                if isTextFilename(delta.getFilename()):
                    originalContents = delta.getFileContents("-k")
                    updatedContents = keywordSubstitution(originalContents)
                    if originalContents != updatedContents:
                        handler, baton = fs.apply_textdelta(root,
                                                     delta.getRepositoryName(),
                                                     None, None, subpool)
                        svn.delta.svn_txdelta_send_string(updatedContents,
                                                     handler, baton, subpool)
                        print "sending ", delta.getRepositoryName()

            print "committing version ",
            print self._commit(revision, delta.getDate(), transaction, subpool)
            core.svn_pool_destroy(subpool)


def deltaSort(deltaOne, deltaTwo):
    """ Sort two deltas based on their time. """
    if time.mktime(deltaOne.date) < time.mktime(deltaTwo.date):
        return -1
    if time.mktime(deltaOne.date) > time.mktime(deltaTwo.date):
        return 1
    return 0

def parseSCCSLog(filename):
    """ Parse the SCCS log for the filename, and add all of its
    deltas to the global array versions. """
    startOfDeltaMarker="start091283123"
    endOfCommentMarker="endofcomment9123klfdgdfg;kdfg\n"
    sccsPrsCommand = "sccs prs -e -d\"%s\\t:I:\\t:P:\\t:D:\t:T:\\n:C:\\n%s\" " % (startOfDeltaMarker, endOfCommentMarker)
    output = os.popen(sccsPrsCommand + filename)
    log = output.readlines()
    output.close()
    commentMode = 0
    comments = ""
    for i in log:
        if i[0:len(endOfCommentMarker)] == endOfCommentMarker:
            versions.append(SCCSDelta(filename, version, user, dateTime, comments))
            commentMode = 0
            comments = ""

        if commentMode:
            comments += i

        if i[0:len(startOfDeltaMarker)] == startOfDeltaMarker:
            commentMode = 1
            (dummy, version, user, date, ti) = i.split("\t",4)
            ti = ti.rstrip()
            dateTime = time.strptime(date + " " + ti, "%y/%m/%d %H:%M:%S")
            

def visitSCCSRepository(interface,dirname,names):
    """ Visit each file in the SCCS directory, calling parseSCCSLog for each."""
    if os.path.split(dirname)[-1] == "SCCS":
        print "Visiting ", dirname
        for i in names:
            if i[0:2] == "s.":
                filename = os.path.join(dirname, i)
                parseSCCSLog(filename)


def run(pool):

    SCCSDelta.rootDirectory = options.sccs_repository
    interface = SVNInterface(options.svn_repository, pool)
    os.path.walk(SCCSDelta.rootDirectory, visitSCCSRepository, interface)
    versions.sort(deltaSort)

    print "Read",
    print len(versions),
    print "versions."

    if len(versions) == 0:
        print "Unable to read any SCCS versions; nothing to convert"
        sys.exit(1)

    # Merge deltas together.
    mergedVersions = [[versions[0]]]
    i = 0
    while i < len(versions):
        if versions[i].match(mergedVersions[-1][-1]):
            mergedVersions[-1].append(versions[i])
        else:
            mergedVersions.append([versions[i]])
        i += 1

    print "consolidated length = ", len(mergedVersions)
    
    # Add each delta.
    for i in mergedVersions:
        interface.add(i)
    
    # Get all of the filenames.
    filenames = {}
    for i in versions:
        filenames[i.getRepositoryName()] = i

    # Update their properties and keywords.
    interface.propertyUpdate(filenames.keys())
    interface.idKeyUpdate(filenames.values())

    # Delete any file ending in '-'
    #versionsToDelete = {}
    #for i in versions:
    #    if i.pathname[-1] == '-':
    #        versionsToDelete[i.getRepositoryName()] = 1
    #interface.remove(versionsToDelete.keys())

    # Delete any directories ending in '-'
    #directoriesToDelete = {}
    #for i in versions:
    #    if os.path.dirname(i.getRepositoryName())[-1] == '-':
    #        directoriesToDelete[os.path.dirname(i.getRepositoryName())] = 1
    #print directoriesToDelete.keys()
    #interface.remove(directoriesToDelete.keys())

if __name__ == '__main__':
    parser = OptionParser(usage="usage: %prog options")
    parser.add_option("-u", "--user", dest="userid", metavar="userid",
                      help="The user id used for sccs2svn generated changes")
    parser.add_option("-o", "--svn-repository", dest="svn_repository",
                      metavar="svn repository directory",
                      help="The location of the Subversion repository; this location will be destroyed!")
    parser.add_option("-i", "--sccs-repository", dest="sccs_repository",
                      metavar="sccs root directory",
                      help="The location of the SCCS repository")
    
    (options, args) = parser.parse_args()

    # Make sure that we have all of the options we need.
    if options.userid == None:
        parser.error("You must supply a user id with --user")

    if options.svn_repository == None:
        parser.error("You must supply a Subversion repository with --svn-repository")

    if options.sccs_repository == None:
        parser.error("You must supply a SCCS repository with --sccs-repository")

    if os.path.exists(options.svn_repository):
        print "Repository directory %s already exists!" % options.svn_repository
        print "Exiting."
        sys.exit(1)
        
    os.system("svnadmin create " + options.svn_repository)


    core.run_app(run)
    
