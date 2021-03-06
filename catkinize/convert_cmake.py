#
# Copyright (c) 2012, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the Willow Garage, Inc. nor the names of its
#       contributors may be used to endorse or promote products derived from
#       this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#


from __future__ import print_function
import re
import os
import sys
import xml.etree.ElementTree as ET

# removals and stuff we can replace
conversions = [
    ('rosbuild_init', None),
    ('cmake_minimum_required', None),
    ('rosbuild_add_boost_directories', None),
    ('rosbuild_add_gtest_build_flags', None),
    ('rosbuild_add_rostest', None),
    ('rosbuild_add_gtest', 'catkin_add_gtest'),
    ('rosbuild_add_pyunit', 'catkin_add_nosetests'),
    ('rosbuild_add_executable', 'add_executable'),
    ('rosbuild_add_library', 'add_library'),
    ('rosbuild_download_test_data', 'download_test_data'),
#    ('rosbuild_', '')
]

substitutions = [
    ('rosbuild_add_openmp_flags()', 'find_package(OpenMP)')]

# stuff that the user has to fix maunally
manual_conversions = [
    ('rosbuild_add_link_flags', '# use link_directories() include_directories(), add_definitions(), target_link_libraries() or set_target_properties'),
    ('rosbuild_remove_link_flags', '# use link_directories() include_directories(), add_definitions(), target_link_libraries() or set_target_properties'),
    ('rosbuild_add_compile_flags', '# use link_directories() include_directories(), add_definitions(), target_link_libraries() or set_target_properties'),
    ('rosbuild_remove_compile_flags', '# use link_directories() include_directories(), add_definitions(), target_link_libraries() or set_target_properties'),
    ('rosbuild_check_for_sse', '# Find other way to find SSE'),
    ('rosbuild_include', '# use include(module) after finding the path'),
    ('rosbuild_add_swigpy_library', '# find swigpy in some other way'),
    ('rosbuild_make_distribution', '# use bloom tool')
    ]

# adding ^ to the beginning of the re would discard all commented lines
FUNCALL_PATTERN = re.compile(r'([ ]*[a-zA-Z][a-zA-Z_]+)(\s*\([^)]*\))', re.MULTILINE)


def chunks(l, n):
    """
    returns a list of n-szed chunks of list l

    >>> chunks([], 3)
    []
    >>> chunks([2, 5, 7], 3)
    [[2, 5, 7]]
    >>> chunks([2, 5, 7, 4, 6, 8], 3)
    [[2, 5, 7], [4, 6, 8]]
    """
    return [l[i:i + n] for i in range(0, len(l), n)]


def convert_cmake(project_path, cmakelists_path=None, manifest_xml_path=None):
    project_name = os.path.basename(os.path.abspath(project_path))
    if not cmakelists_path:
        cmakelists_path = os.path.join(project_path, 'CMakeLists.txt')
    if not manifest_xml_path:
        manifest_xml_path = os.path.join(project_path, 'manifest.xml')

    print('Converting %s' % cmakelists_path, file=sys.stderr)
    with open(cmakelists_path, 'r') as f_in:
        content = f_in.read()

    dependencies_str = ' '.join(get_dependencies(manifest_xml_path))

    # anything that looks like a macro or function call (broken for nested round parens)
    tokens = FUNCALL_PATTERN.split(content)

    # storing the originals allows interactive mode where user confirms each change
    result = tokens[:1]
    original = tokens[:1]
    boost_components = set()

    # find replacement for each snippet. Chunks are (funcall, argslist, otherlines)
    first_boost = -1
    for count, (name, fun_args, rest) in enumerate(chunks(tokens[1:], 3)):
        oldsnippet = '%s%s' % (name, fun_args)
        original.append(oldsnippet)
        newsnippet, components = convert_boost_snippet(name, fun_args)
        if newsnippet is None:
            newsnippet = convert_snippet(name, fun_args)
            if newsnippet != oldsnippet:
                result.append(newsnippet)
            else:
                result.append(None)
        else:
            if first_boost < 0:
                first_boost = count * 2 + 1
            boost_components = boost_components.union(components)
            result.append(newsnippet)

        result.append(None)
        original.append(rest)

    if boost_components:
        # reverse order due to insert
        result.insert(first_boost, 'include_directories(${Boost_INCLUDE_DIRS})\n')
        result.insert(first_boost, 'find_package(Boost REQUIRED COMPONENTS %s)\n' % ' '.join(boost_components))
        original.insert(first_boost, '')
        original.insert(first_boost, '')

    result_string = ''
    lines = content.splitlines()
    if not [l for l in lines if 'catkin_package' in l]:
        result_string += ('\n'.join(make_header_lines(project_name, dependencies_str)))

    for (old_snippet, new_snippet) in zip(original, result):
        if old_snippet or new_snippet:
            result_string += (new_snippet or old_snippet)

    with_messages = ('add_message_files' in result or 'add_service_files' in result_string)
    result_string += make_package_lines(dependencies_str, with_messages)
    return result_string


def get_dependencies(manifest_path):
    '''
    Given a path to a manifest.xml file, get_dependencies() parses the file and
    yields all dependencies listed in it.
    '''
    with open(manifest_path) as file:
        tree = ET.XML(file.read())
        for tag in tree.findall('depend'):
            pkg = tag.attrib.get('package')
            if pkg:
                yield pkg
        for tag in tree.findall('rosdep'):
            pkg = tag.attrib.get('name')
            if pkg:
                yield pkg


def make_header_lines(project_name, deps_str):
    """
    Make top lines of CMakeLists file according to
    http://www.ros.org/doc/groovy/api/catkin/html/user_guide/standards.html
    """
    components_str = 'COMPONENTS %s' % deps_str if deps_str.strip() else ''
    header = '''
# http://ros.org/doc/groovy/api/catkin/html/user_guide/supposed.html
cmake_minimum_required(VERSION 2.8.3)
project(%s)
# Load catkin and all dependencies required for this package
# TODO: remove all from COMPONENTS that are not catkin packages.
find_package(catkin REQUIRED %s)

# include_directories(include ${Boost_INCLUDE_DIR} ${catkin_INCLUDE_DIRS})
''' % (project_name, components_str)
    return header.strip().splitlines()


def make_package_lines(deps_str, with_messages):
    full_deps_str = 'DEPENDS %s' % deps_str if deps_str.strip() else 'DEPENDS  # TODO'
    msg_str = '## Generate added messages and services with any dependencies listed here\ngenerate_messages(\n  #TODO DEPENDENCIES geometry_msgs std_msgs\n)' if with_messages else ''
    lines = '''%s
# TODO: fill in what other packages will need to use this package
## DEPENDS: system dependencies of this project that dependent projects also need
## CATKIN_DEPENDS: catkin_packages dependent projects also need
## INCLUDE_DIRS: 
## LIBRARIES: libraries you create in this project that dependent projects also need
catkin_package(
    %s
    CATKIN_DEPENDS # TODO
    INCLUDE_DIRS # TODO include
    LIBRARIES # TODO
)''' % (msg_str, full_deps_str)
    return lines


def convert_snippet(name, funargs):
    """
    Do all replacements that can be done for a single snippet without looking at
    anything else.
    """
    snippet = '%s%s' % (name, funargs)
    converted = False
    for a, b in conversions:
        if a == name.strip():
            if b is not None:
                snippet = snippet.replace(a, b)
            else:
                snippet = comment(snippet, '\n# CATKIN_MIGRATION: removed during catkin migration')
            converted = True
            break
    if not converted:
        for a, b in manual_conversions:
            if a == name.strip():
                snippet = comment(snippet, '\n# CATKIN_MIGRATION\n%s' % b)
                converted = True
                break
    if not converted:
        if 'include' == name.strip():
            if 'rosbuild' in funargs:
                snippet = comment(snippet, '\n# CATKIN_MIGRATION: removed during catkin migration')
            converted = True
    if not converted:
        if 'rosbuild_genmsg' == name.strip():
            snippet = 'add_message_files(\n  FILES\n  # TODO: List your msg files here\n)'
            converted = True
        elif 'rosbuild_gensrv' == name.strip():
            snippet = 'add_service_files(\n  FILES\n  # TODO: List your msg files here\n)'
            converted = True
    return snippet


def comment(snippet, header):
    """
    comments out a snippet and adds a comment saying so
    >>> comment('foo(bar)', '# gone')
    '# gone\\n# foo(bar)'
    """
    result = []
    if header:
        result.append(header)
    for line in snippet.splitlines():
        result.append('# %s' % line)
    return '\n'. join(result)


# separates target from components
ARGUMENT_SPLITTER = re.compile(r'\s*\(\s*([^\s]+)\s+([^)]+)\)')


def convert_boost_snippet(name, args):
    """
    convert_cmakelists Boost sections.
    """
    realname = name.strip()
    if realname == 'rosbuild_link_boost':
        # rosbuild_link_boost snippets expand to multiple statements.
        m = ARGUMENT_SPLITTER.match(args)
        if not m:
            raise ValueError('Could not recognize rosbuild_link_boost arguments (maybe multi-line?): \n%s' % args)
        target = m.group(1)
        components = m.group(2)
        return "target_link_libraries(%s ${Boost_LIBRARIES})" % (target), components.split()
    return None, None
