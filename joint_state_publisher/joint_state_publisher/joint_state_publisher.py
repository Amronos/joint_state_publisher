# Copyright (c) 2010, Willow Garage, Inc.
# All rights reserved.
#
# Software License Agreement (BSD License 2.0)
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Willow Garage, Inc. nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# Standard Python imports
import argparse
import math
import sys
import xml.dom.minidom

# Third-party imports
import packaging.version

# ROS 2 imports
from rcl_interfaces.msg import ParameterDescriptor, ParameterType
import rclpy
import rclpy.node
import sensor_msgs.msg
import std_msgs.msg


def _convert_to_float(name, jtype, limit_name, input_string):
    try:
        return float(input_string)
    except ValueError as e:
        raise Exception(
            f'"{limit_name}" limit must be a float for joint "{name}" of type "{jtype}"') from e


class JointStatePublisher(rclpy.node.Node):

    def get_param(self, name):
        return self.get_parameter(name).value

    def _init_joint(self, minval, maxval, zeroval):
        joint = {'min': minval, 'max': maxval, 'zero': zeroval}
        if self.pub_def_positions:
            joint['position'] = zeroval
        if self.pub_def_vels:
            joint['velocity'] = 0.0
        if self.pub_def_efforts:
            joint['effort'] = 0.0
        return joint

    def init_sdf(self, xmldom):
        free_joints = {}
        joint_list = []
        dependent_joints = {}

        model_list = xmldom.getElementsByTagName('model')
        if not model_list:
            raise Exception('SDF must have a "model" tag')
        model = model_list[0]
        # Find all non-fixed joints
        for child in model.childNodes:
            if child.nodeType is child.TEXT_NODE:
                continue
            if child.localName != 'joint':
                continue
            jtype = child.getAttribute('type')
            if jtype in ('gearbox', 'revolute2', 'ball', 'screw', 'universal', 'fixed'):
                continue
            name = child.getAttribute('name')

            # joint limits
            if jtype == 'continuous':
                minval = -math.pi
                maxval = math.pi
            else:
                # Limits are required, and required to be floats.
                limit_list = child.getElementsByTagName('limit')
                if not limit_list:
                    raise Exception(
                        f'Limits must be specified for joint "{name}" of type "{jtype}"')
                limit = limit_list[0]

                lower_list = limit.getElementsByTagName('lower')
                if not lower_list:
                    raise Exception(
                        f'"lower" limit must be specified for joint "{name}" of type "{jtype}"')
                lower = lower_list[0]
                minval = _convert_to_float(name, jtype, 'lower', lower.firstChild.data)

                upper_list = limit.getElementsByTagName('upper')
                if not upper_list:
                    raise Exception(
                        f'"upper" limit must be specified for joint "{name}" of type "{jtype}"')
                upper = upper_list[0]
                maxval = _convert_to_float(name, jtype, 'upper', upper.firstChild.data)

                if minval == float('-inf'):
                    minval = -3.14
                if maxval == float('inf'):
                    maxval = 3.14

            if self.zeros and name in self.zeros:
                zeroval = self.zeros[name]
            elif minval > 0 or maxval < 0:
                zeroval = (maxval + minval)/2
            else:
                zeroval = 0

            joint = self._init_joint(minval, maxval, zeroval)

            if jtype == 'continuous':
                joint['continuous'] = True
            free_joints[name] = joint
            joint_list.append(name)

        return (free_joints, joint_list, dependent_joints)

    def init_collada(self, xmldom):
        free_joints = {}
        joint_list = []
        dependent_joints = {}

        colladadom = xmldom.childNodes[0]

        if not colladadom.hasAttribute('version'):
            raise Exception('COLLADA must have a version tag')

        colladaversion = packaging.version.parse(colladadom.attributes['version'].value)
        if colladaversion < packaging.version.parse('1.5.0'):
            raise Exception('COLLADA must be at least version 1.5.0')

        kinematics_model_list = xmldom.getElementsByTagName('kinematics_model')
        if not kinematics_model_list:
            raise Exception('COLLADA must have a "kinematics_model" tag')
        kinematics_model = kinematics_model_list[0]
        technique_common_list = kinematics_model.getElementsByTagName('technique_common')
        if not technique_common_list:
            raise Exception('COLLADA must have a "technique_common" tag')
        technique_common = technique_common_list[0]

        for child in technique_common.childNodes:
            if child.nodeType is child.TEXT_NODE:
                continue
            if child.localName != 'joint':
                continue

            name = child.getAttribute('name')
            revolute_list = child.getElementsByTagName('revolute')
            if not revolute_list:
                continue
            revolute = revolute_list[0]

            limit_list = revolute.getElementsByTagName('limits')
            if not limit_list:
                raise Exception(f'Limits must be specified for joint "{name}" of type "revolute"')

            limit = limit_list[0]

            min_list = limit.getElementsByTagName('min')
            if not min_list:
                raise Exception(
                    f'"min" limit must be specified for joint "{name}" of type "revolute"')
            minval = _convert_to_float(name, 'revolute', 'min',
                                       min_list[0].childNodes[0].nodeValue)

            max_list = limit.getElementsByTagName('max')
            if not max_list:
                raise Exception(
                    f'"max" limit must be specified for joint "{name}" of type "revolute"')
            maxval = _convert_to_float(name, 'revolute', 'max',
                                       max_list[0].childNodes[0].nodeValue)

            if minval == maxval:  # this is a fixed joint
                continue

            joint_list.append(name)
            minval *= math.pi/180.0
            maxval *= math.pi/180.0
            free_joints[name] = self._init_joint(minval, maxval, 0.0)

        return (free_joints, joint_list, dependent_joints)

    def init_urdf(self, xmldom):
        free_joints = {}
        joint_list = []
        dependent_joints = {}

        robot_list = xmldom.getElementsByTagName('robot')
        if not robot_list:
            raise Exception('URDF must have a "robot" tag')
        robot = robot_list[0]
        # Find all non-fixed joints
        for child in robot.childNodes:
            if child.nodeType is child.TEXT_NODE:
                continue
            if child.localName != 'joint':
                continue
            jtype = child.getAttribute('type')
            if jtype in ('fixed', 'floating', 'planar'):
                continue
            name = child.getAttribute('name')
            if jtype == 'continuous':
                minval = -math.pi
                maxval = math.pi
            else:
                # Limits are required, and required to be floats.
                limit_list = child.getElementsByTagName('limit')
                if not limit_list:
                    raise Exception(
                        f'Limits must be specified for joint "{name}" of type "{jtype}"')

                limit = limit_list[0]

                if not limit.hasAttribute('lower'):
                    raise Exception(
                        f'"lower" limit must be specified for joint "{name}" of type "{jtype}"')
                minval = _convert_to_float(name, jtype, 'lower', limit.getAttribute('lower'))

                if not limit.hasAttribute('upper'):
                    raise Exception(
                        f'"upper" limit must be specified for joint "{name}" of type "{jtype}"')
                maxval = _convert_to_float(name, jtype, 'upper', limit.getAttribute('upper'))

            safety_tags = child.getElementsByTagName('safety_controller')
            if self.use_small and len(safety_tags) == 1:
                tag = safety_tags[0]
                if tag.hasAttribute('soft_lower_limit'):
                    minval = max(minval, float(tag.getAttribute('soft_lower_limit')))
                if tag.hasAttribute('soft_upper_limit'):
                    maxval = min(maxval, float(tag.getAttribute('soft_upper_limit')))

            joint_list.append(name)

            mimic_tags = child.getElementsByTagName('mimic')
            if self.use_mimic and len(mimic_tags) == 1:
                tag = mimic_tags[0]
                entry = {'parent': tag.getAttribute('joint')}
                if tag.hasAttribute('multiplier'):
                    entry['factor'] = float(tag.getAttribute('multiplier'))
                if tag.hasAttribute('offset'):
                    entry['offset'] = float(tag.getAttribute('offset'))

                dependent_joints[name] = entry
                continue

            if name in dependent_joints:
                continue

            if self.zeros and name in self.zeros:
                zeroval = self.zeros[name]
            elif minval > 0 or maxval < 0:
                zeroval = (maxval + minval)/2
            else:
                zeroval = 0

            joint = self._init_joint(minval, maxval, zeroval)

            if jtype == 'continuous':
                joint['continuous'] = True
            free_joints[name] = joint

        return (free_joints, joint_list, dependent_joints)

    def configure_robot(self, description):
        self.get_logger().debug('Got description, configuring robot')
        xmldom = xml.dom.minidom.parseString(description)

        root = xmldom.documentElement
        if root.tagName == 'sdf':
            (free_joints, joint_list, dependent_joints) = self.init_sdf(xmldom)
        elif root.tagName == 'COLLADA':
            (free_joints, joint_list, dependent_joints) = self.init_collada(xmldom)
        else:
            (free_joints, joint_list, dependent_joints) = self.init_urdf(xmldom)

        self.free_joints = free_joints
        self.joint_list = joint_list  # for maintaining the original order of the joints
        self.dependent_joints = dependent_joints

        if self.robot_description_update_cb is not None:
            self.robot_description_update_cb()

    def parse_dependent_joints(self):
        dj = {}
        dependent_joints = self.get_parameters_by_prefix('dependent_joints')
        # get_parameters_by_prefix returned a dictionary of keynames like
        # 'head.parent', 'head.offset', etc. that map to rclpy Parameter values.
        # The rest of the code assumes that the dependent_joints dictionary is
        # a map of name -> dict['parent': parent, factor: factor, offset: offset],
        # where both factor and offset are optional.  Thus we parse the values we
        # got into that structure.
        allowed_joint_names = ('parent', 'factor', 'offset')
        for name, param in dependent_joints.items():
            # First split on the dots; there should be one and exactly one dot
            split = name.split('.')
            if len(split) != 2:
                raise Exception("Invalid dependent_joint name '%s'" % (name))
            newkey = split[0]
            newvalue = split[1]
            if newvalue not in allowed_joint_names:
                allowed_joint_string = ', '.join(f"'{w}'" for w in allowed_joint_names)
                raise Exception("Invalid dependent_joint name '%s' "
                                '(allowed values are %s)' % (newvalue, allowed_joint_string))
            if newkey in dj:
                dj[newkey].update({newvalue: param.value})
            else:
                dj[newkey] = {newvalue: param.value}

        # Now ensure that there is at least a 'parent' in all keys
        for name, outdict in dj.items():
            if outdict.get('parent', None) is None:
                raise Exception('All dependent_joints must at least have a parent')

        return dj

    def declare_ros_parameter(self, name, default, descriptor):
        # When the automatically_declare_parameters_from_overrides parameter to
        # rclpy.create_node() is True, then any parameters passed in on the
        # command-line are automatically declared.  In that case, calling
        # node.declare_parameter() will raise an exception.  However, in the case
        # where a parameter is *not* overridden, we still want to declare it
        # (so it shows up in "ros2 param list", for instance).  Thus we always do
        # a declaration and just ignore ParameterAlreadyDeclaredException.

        try:
            self.declare_parameter(name, default, descriptor)
        except rclpy.exceptions.ParameterAlreadyDeclaredException:
            pass

    def robot_description_cb(self, msg):
        try:
            self.configure_robot(msg.data)
        except Exception as e:
            self.get_logger().warn(str(e))

    def __init__(self, description_file):
        super().__init__('joint_state_publisher',
                         automatically_declare_parameters_from_overrides=True)

        self.declare_ros_parameter('publish_default_efforts', False,
                                   ParameterDescriptor(type=ParameterType.PARAMETER_BOOL))
        self.declare_ros_parameter('publish_default_positions', True,
                                   ParameterDescriptor(type=ParameterType.PARAMETER_BOOL))
        self.declare_ros_parameter('publish_default_velocities', False,
                                   ParameterDescriptor(type=ParameterType.PARAMETER_BOOL))
        self.declare_ros_parameter('rate', 10,
                                   ParameterDescriptor(type=ParameterType.PARAMETER_INTEGER))
        self.declare_ros_parameter('source_list', [],
                                   ParameterDescriptor(type=ParameterType.PARAMETER_STRING_ARRAY))
        self.declare_ros_parameter('use_mimic_tags', True,
                                   ParameterDescriptor(type=ParameterType.PARAMETER_BOOL))
        self.declare_ros_parameter('use_smallest_joint_limits', True,
                                   ParameterDescriptor(type=ParameterType.PARAMETER_BOOL))
        self.declare_ros_parameter('delta', 0.0,
                                   ParameterDescriptor(type=ParameterType.PARAMETER_DOUBLE))
        # In theory we would also declare 'dependent_joints' and 'zeros' here.
        # Since rclpy doesn't support maps natively, though, we just end up
        # letting 'automatically_declare_parameters_from_overrides' declare
        # any parameters for us.

        self.free_joints = {}
        self.joint_list = []  # for maintaining the original order of the joints
        self.dependent_joints = self.parse_dependent_joints()
        self.use_mimic = self.get_param('use_mimic_tags')
        self.use_small = self.get_param('use_smallest_joint_limits')

        zeros = self.get_parameters_by_prefix('zeros')
        # get_parameters_by_prefix() returns a map of name -> Parameter
        # structures, but self.zeros is expected to be a list of name -> float;
        # fix that here.
        self.zeros = {k: v.value for (k, v) in zeros.items()}

        self.pub_def_positions = self.get_param('publish_default_positions')
        self.pub_def_vels = self.get_param('publish_default_velocities')
        self.pub_def_efforts = self.get_param('publish_default_efforts')

        self.robot_description_update_cb = None

        if description_file is not None:
            # If we were given a URDF file on the command-line, use that.
            with open(description_file, 'r') as infp:
                description = infp.read()
            self.configure_robot(description)
        else:
            self.get_logger().info(
                'Waiting for robot_description to be published on the robot_description topic...')

        # In all cases, subscribe to the '/robot_description' topic; this allows us to get our
        # initial configuration in the case we weren't given it on the command-line, and allows
        # us to dynamically update later.
        qos = rclpy.qos.QoSProfile(depth=1,
                                   durability=rclpy.qos.QoSDurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(std_msgs.msg.String,
                                 'robot_description',
                                 lambda msg: self.robot_description_cb(msg),
                                 qos)

        self.delta = self.get_param('delta')

        source_list = self.get_param('source_list')
        self.sources = []
        for source in source_list:
            self.sources.append(self.create_subscription(sensor_msgs.msg.JointState, source,
                                                         self.source_cb, 10))

        # The source_update_cb will be called at the end of self.source_cb.
        # The main purpose is to allow external observers (such as the
        # joint_state_publisher_gui) to be notified when things are updated.
        self.source_update_cb = None

        self.pub = self.create_publisher(sensor_msgs.msg.JointState, 'joint_states', 10)

        self.timer = self.create_timer(1.0 / self.get_param('rate'), self.timer_callback)

    def source_cb(self, msg):
        for i in range(len(msg.name)):
            name = msg.name[i]
            if name not in self.free_joints:
                continue

            if msg.position:
                position = msg.position[i]
            else:
                position = None
            if msg.velocity:
                velocity = msg.velocity[i]
            else:
                velocity = None
            if msg.effort:
                effort = msg.effort[i]
            else:
                effort = None

            joint = self.free_joints[name]
            if position is not None:
                joint['position'] = position
            if velocity is not None:
                joint['velocity'] = velocity
            if effort is not None:
                joint['effort'] = effort

        if self.source_update_cb is not None:
            self.source_update_cb()

    def set_source_update_cb(self, user_cb):
        self.source_update_cb = user_cb

    def set_robot_description_update_cb(self, user_cb):
        self.robot_description_update_cb = user_cb

    def timer_callback(self):
        # Publish Joint States
        msg = sensor_msgs.msg.JointState()
        msg.header.stamp = self.get_clock().now().to_msg()

        if self.delta > 0:
            self.update(self.delta)

        # Initialize msg.position, msg.velocity, and msg.effort.
        has_position = len(self.dependent_joints.items()) > 0
        has_velocity = False
        has_effort = False
        for name, joint in self.free_joints.items():
            if not has_position and 'position' in joint:
                has_position = True
            if not has_velocity and 'velocity' in joint:
                has_velocity = True
            if not has_effort and 'effort' in joint:
                has_effort = True
        num_joints = (len(self.free_joints.items()) +
                      len(self.dependent_joints.items()))
        if has_position:
            msg.position = num_joints * [0.0]
        if has_velocity:
            msg.velocity = num_joints * [0.0]
        if has_effort:
            msg.effort = num_joints * [0.0]

        for i, name in enumerate(self.joint_list):
            msg.name.append(str(name))
            joint = None

            # Add Free Joint
            if name in self.free_joints:
                joint = self.free_joints[name]
                factor = 1
                offset = 0
            # Add Dependent Joint
            elif name in self.dependent_joints:
                param = self.dependent_joints[name]
                parent = param['parent']
                factor = param.get('factor', 1.0)
                offset = param.get('offset', 0.0)
                # Handle recursive mimic chain
                recursive_mimic_chain_joints = [name]
                while parent in self.dependent_joints:
                    if parent in recursive_mimic_chain_joints:
                        error_message = 'Found an infinite recursive mimic chain'
                        self.get_logger().error(
                            f'{error_message}: {recursive_mimic_chain_joints + [parent]}')
                        sys.exit(1)
                    recursive_mimic_chain_joints.append(parent)
                    param = self.dependent_joints[parent]
                    parent = param['parent']
                    offset += factor * param.get('offset', 0)
                    factor *= param.get('factor', 1)
                joint = self.free_joints[parent]

            if has_position and 'position' in joint:
                msg.position[i] = float(joint['position']) * factor + offset
            if has_velocity and 'velocity' in joint:
                msg.velocity[i] = float(joint['velocity']) * factor
            if has_effort and 'effort' in joint:
                msg.effort[i] = float(joint['effort'])

        if msg.name or msg.position or msg.velocity or msg.effort:
            # Only publish non-empty messages
            self.pub.publish(msg)

    def update(self, delta):
        for name, joint in self.free_joints.items():
            forward = joint.get('forward', True)
            if forward:
                joint['position'] += delta
                if joint['position'] > joint['max']:
                    if joint.get('continuous', False):
                        joint['position'] = joint['min']
                    else:
                        joint['position'] = joint['max']
                        joint['forward'] = not forward
            else:
                joint['position'] -= delta
                if joint['position'] < joint['min']:
                    joint['position'] = joint['min']
                    joint['forward'] = not forward


def main():
    # Initialize rclpy with the command-line arguments
    rclpy.init()

    # Strip off the ROS 2-specific command-line arguments
    stripped_args = rclpy.utilities.remove_ros_args(args=sys.argv)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'description_file', help='Robot description file to use', nargs='?', default=None)

    # Parse the remaining arguments, noting that the passed-in args must *not*
    # contain the name of the program.
    parsed_args = parser.parse_args(args=stripped_args[1:])
    jsp = JointStatePublisher(parsed_args.description_file)

    try:
        rclpy.spin(jsp)
    except KeyboardInterrupt:
        pass

    jsp.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
