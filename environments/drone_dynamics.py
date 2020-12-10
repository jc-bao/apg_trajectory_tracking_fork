import torch
import numpy as np
try:
    from .copter import copter_params
except ImportError:
    from copter import copter_params
from types import SimpleNamespace
copter_params = SimpleNamespace(**copter_params)


def world_to_body_matrix(attitude):
    """
    Creates a transformation matrix for directions from world frame
    to body frame for a body with attitude given by `euler` Euler angles.
    :param euler: The Euler angles of the body frame.
    :return: The transformation matrix.
    """

    # check if we have a cached result already available
    roll = attitude[:, 0]
    pitch = attitude[:, 1]
    yaw = attitude[:, 2]

    Cy = torch.cos(yaw)
    Sy = torch.sin(yaw)
    Cp = torch.cos(pitch)
    Sp = torch.sin(pitch)
    Cr = torch.cos(roll)
    Sr = torch.sin(roll)

    # create matrix
    m1 = torch.transpose(torch.vstack([Cy * Cp, Sy * Cp, -Sp]), 0, 1)
    m2 = torch.transpose(
        torch.vstack(
            [Cy * Sp * Sr - Cr * Sy, Cr * Cy + Sr * Sy * Sp, Cp * Sr]
        ), 0, 1
    )
    m3 = torch.transpose(
        torch.vstack(
            [Cy * Sp * Cr + Sr * Sy, Cr * Sy * Sp - Cy * Sr, Cr * Cp]
        ), 0, 1
    )
    matrix = torch.stack((m1, m2, m3), dim=1)

    return matrix


def linear_dynamics(rotor_speed, attitude, velocity):
    """
    Calculates the linear acceleration of a quadcopter with parameters
    `copter_params` that is currently in the dynamics state composed of:
    :param rotor_speed: current rotor speeds
    :param attitude: current attitude
    :param velocity: current velocity
    :return: Linear acceleration in world frame.
    """
    m = copter_params.mass
    b = copter_params.thrust_factor
    Kt = torch.from_numpy(copter_params.translational_drag)

    world_to_body = world_to_body_matrix(attitude)
    body_to_world = torch.transpose(world_to_body, 1, 2)

    squared_speed = torch.sum(rotor_speed**2, axis=1)
    constant_vec = torch.zeros(3)
    constant_vec[2] = 1

    thrust = b / m * torch.mul(
        torch.matmul(body_to_world, constant_vec).t(), squared_speed
    ).t()
    # print(body_to_world.size(), constant_vec.size(), "res:", thrust.size())
    # print(body_to_world.size(), torch.diag(Kt).size(), world_to_body.size())
    Ktw = torch.matmul(
        body_to_world, torch.matmul(torch.diag(Kt).float(), world_to_body)
    )
    # print("ktw", Ktw.size(), "veloc", velocity.size())
    drag = torch.squeeze(torch.matmul(Ktw, torch.unsqueeze(velocity, 2)) / m)
    # print("drag size", drag.size(), "thrust", thrust.size())
    thrust_minus_drag = thrust - drag + torch.from_numpy(copter_params.gravity)
    # version for batch size 1 (working version)
    # summed = torch.add(
    #     torch.transpose(drag * (-1), 0, 1), thrust
    # ) + copter_params.gravity
    # print("output linear", thrust_minus_drag.size())
    return thrust_minus_drag


def to_euler_matrix(attitude):
    # attitude is [roll, pitch, yaw]
    pitch = attitude[:, 1]
    roll = attitude[:, 0]
    Cp = torch.cos(pitch)
    Sp = torch.sin(pitch)
    Cr = torch.cos(roll)
    Sr = torch.sin(roll)

    # create matrix
    m1 = torch.transpose(
        torch.vstack([torch.ones(Sp.size()),
                      torch.zeros(Sp.size()), -Sp]), 0, 1
    )
    m2 = torch.transpose(
        torch.vstack([torch.zeros(Sr.size()), Cr, Cp * Sr]), 0, 1
    )
    m3 = torch.transpose(
        torch.vstack([torch.zeros(Sr.size()), -Sr, Cp * Cr]), 0, 1
    )
    matrix = torch.stack((m1, m2, m3), dim=1)

    # matrix = torch.tensor([[1, 0, -Sp], [0, Cr, Cp * Sr], [0, -Sr, Cp * Cr]])
    return matrix


def euler_rate(attitude, angular_velocity):
    euler_matrix = to_euler_matrix(attitude)
    # print(
    #     "euler matrix", euler_matrix.size(), "av",
    #     torch.unsqueeze(angular_velocity, 2).size()
    # )
    together = torch.matmul(
        euler_matrix, torch.unsqueeze(angular_velocity.float(), 2)
    )
    # print("output euler rate", together.size())
    return torch.squeeze(together)


def propeller_torques(rotor_speeds):
    """
    Calculates the torques that are directly generated by the propellers.
    :return:
    """
    # squared
    squared_speeds = rotor_speeds**2
    r0 = squared_speeds[:, 0]
    r1 = squared_speeds[:, 1]
    r2 = squared_speeds[:, 2]
    r3 = squared_speeds[:, 3]

    Lb = copter_params.arm_length * copter_params.thrust_factor
    d = copter_params.drag_factor
    motor_torque = r3 + r1 - r2 - r0
    # print(motor_torque.size())
    B = torch.stack([Lb * (r3 - r1), Lb * (r0 - r2), d * motor_torque]).t()
    # print("propeller torque outputs:", B.size())
    return B


def net_rotor_speed(rotorspeeds):
    """
    Calculate net rotor speeds (subtract 2 from other 2)
    """
    return (
        rotorspeeds[:, 0] - rotorspeeds[:, 1] + rotorspeeds[:, 2] -
        rotorspeeds[:, 3]
    )


def angular_momentum_body_frame(rotor_speeds, angular_velocity):
    """
    Calculates the angular momentum of a quadcopter with parameters
    `copter_params` that is currently in the dynamics state `state`.
    :param av: Current angular velocity.
    :return: angular acceleration in body frame.
    """
    av = angular_velocity
    J = copter_params.rotor_inertia
    Kr = torch.from_numpy(copter_params.rotational_drag)
    inertia = torch.from_numpy(copter_params.frame_inertia).float()

    # this is the wrong shape, should be transposed, but for multipluing later
    # in gyro we would have to transpose again - so don't do it here
    transformed_av = torch.stack(
        (av[:, 2], -av[:, 1], torch.zeros(av.size()[0]))
    )
    # print(
    #     "transformed av", transformed_av.size(),
    #     net_rotor_speed(rotor_speeds).size()
    # )
    # J is scalar, net rotor speed outputs vector of len batch size
    gyro = torch.transpose(
        net_rotor_speed(rotor_speeds) * J * transformed_av, 0, 1
    )
    # print("gyro", gyro.size())
    drag = Kr * av
    # print("drag", drag.size())
    Mp = propeller_torques(rotor_speeds)
    # print("Mp", Mp)
    # print("NUMPY:")
    # print(av.numpy()[0], copter_params.frame_inertia * av.numpy()[0])
    # print(
    #     "cross",
    #     np.cross(av.numpy()[0],
    #              copter_params.frame_inertia * av.numpy()[0])
    # )
    # print("TORCH")
    # print(av, I * av)
    B = Mp - drag + gyro - torch.cross(av, inertia * av, dim=1)
    # print(torch.cross(av, I * av, dim=1).size())
    # print(Mp.size(), drag.size(), gyro.size())
    # print("output angular momentum", B.size())
    return B


def simulate_quadrotor(action, state, dt=0.02):
    """
    Simulate the dynamics of the quadrotor for the timestep given
    in `dt`. First the rotor speeds are updated according to the desired
    rotor speed, and then linear and angular accelerations are calculated
    and integrated.
    :param CopterParams params: Parameters of the quadrotor.
    :param DynamicsState state: Current dynamics state.
    """
    # extract state
    position = state[:, :3]
    attitude = state[:, 3:6]
    velocity = state[:, 6:9]
    rotor_speed = state[:, 9:13]
    desired_rotor_speeds = state[:, 13:17]
    angular_velocity = state[:, 17:20]

    # set desired rotor speeds based on action
    desired_rotor_speeds = torch.sqrt(action) * copter_params.max_rotor_speed

    # let rotor speed approach desired rotor speed and avoid negative rotation
    gamma = 1.0 - 0.5**(dt / copter_params.rotor_speed_half_time)
    dw = gamma * (desired_rotor_speeds - rotor_speed)
    rotor_speed = rotor_speed + dw
    rotor_speed = torch.maximum(rotor_speed, torch.zeros(rotor_speed.size()))

    acceleration = linear_dynamics(rotor_speed, attitude, velocity)

    ang_momentum = angular_momentum_body_frame(rotor_speed, angular_velocity)
    angular_acc = ang_momentum / torch.from_numpy(copter_params.frame_inertia)
    # print(position.size(), dt, acceleration.size(), velocity.size())
    # print(position, dt, acceleration, velocity)
    # update state
    position = position + 0.5 * dt * dt * acceleration + 0.5 * dt * velocity
    # print(position)
    # print("new position", position.size())
    velocity = velocity + dt * acceleration
    angular_velocity = angular_velocity + dt * angular_acc
    attitude = attitude + dt * euler_rate(attitude, angular_velocity)
    # print(
    #     position.shape, attitude.shape, velocity.shape, rotor_speed.shape,
    #     desired_rotor_speeds.shape, angular_velocity.shape
    # )
    state = torch.hstack(
        (
            position, attitude, velocity, rotor_speed, desired_rotor_speeds,
            angular_velocity
        )
    )
    # print("state", state)
    # print("output state", state.size())
    return state.float()
