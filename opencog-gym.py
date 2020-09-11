# OpenCog wrapper around OpenAI Gym.
#
# Highly experimental at this stage.  For now we write an ad-hoc
# wrapper for the CartPole-v0 env, latter on this wrapper will be
# generalized to any env, and the user will have to overload of the
# various methods of that wrapper for specific env.

##############
# Initialize #
##############

# Python
import os
import time
import random

# SciPy
from scipy.stats import beta

# OpenCog
from opencog.atomspace import AtomSpace, TruthValue
from opencog.atomspace import types
from opencog.type_constructors import *
from opencog.pln import *
from opencog.scheme_wrapper import *
a = AtomSpace()
set_default_atomspace(a)

# OpenPsi
from opencog.openpsi import OpenPsi
op = OpenPsi(a)

# OpenAI Gym
import gym
env = gym.make('CartPole-v1')
# Uncomment the following to get a description of env
# help(env.unwrapped)

#############
# Constants #
#############

TRUE_TV = TruthValue(1, 1)
X_ENABLED = 'DISPLAY' in os.environ

#############
# Functions #
#############

def tv_to_beta(tv, prior_a=1, prior_b=1):
    """Convert a truth value to a beta distribution.

    Given a truth value, return the beta distribution that best fits
    it.  Two optional parameters are provided to set the prior of the
    beta-distribution, the default values are prior_a=1 and prior_b=1
    corresponding to the Bayesian prior.

    """

    count = tv.count
    pos_count = count * tv.mean # the mean is actually the mode
    a = prior_a + pos_count
    b = prior_b + count - pos_count
    return beta(a, b)


def tv_rv(tv):
    """Return a first order probability variate of a truth value.

    Return a first order probability variate of the beta-distribution
    representing the second order distribution of tv.

    """

    beta = tv_to_beta(tv)
    return beta.rvs()


def thompson_sample(actdist):
    """Perform Thompson sampling over the action distribution.

    Meaning, for each action truth value, sample its second order
    distribution to obtain a first order probability variate, and
    return the pair (action, pblty) corresponding to the highest
    variate pblty.

    """

    actps = [(action, tv_rv(tv)) for (action, tv) in actdist]
    return max(actps, key=lambda actp: actp[1])


def observation_to_atomese(observation):
    """Translate gym observation to Atomese

    There are 4 observations (taken from CartPoleEnv help)

    Observation               Min             Max
    -----------               ---             ---
    Cart Position             -4.8            4.8
    Cart Velocity             -Inf            Inf
    Pole Angle                -24 deg         24 deg
    Pole Velocity At Tip      -Inf            Inf

    They are represented in atomese as follows

    Evaluation
      Predicate "Cart Position"
      Number CP

    Evaluation
      Predicate "Cart Velocity"
      Number CV

    Evaluation
      Predicate "Pole Angle"
      Number PA

    Evaluation
      Predicate "Pole Velocity At Tip"
      Number PVAT

    Note that the observations are neither tv-set nor timestamped. It
    is up to the caller to do it.

    A python list (not an atomese list) is returned with these 4
    Atomese observations.

    """

    cp = NumberNode(str(observation[0]))
    cv = NumberNode(str(observation[1]))
    pa = NumberNode(str(observation[2]))
    pvat = NumberNode(str(observation[3]))

    return [EvaluationLink(PredicateNode("Cart Position"), cp),
            EvaluationLink(PredicateNode("Cart Velocity"), cv),
            EvaluationLink(PredicateNode("Pole Angle"), pa),
            EvaluationLink(PredicateNode("Cart Velocity At Tip"), pvat)]


def reward_to_atomese(reward):
    """Translate gym reward to Atomese

    Evaluation
      Predicate "Reward"
      Number reward

    The reward representation is neither tv-set nor timestamped. It is
    up to the caller to do it.

    """

    rn = NumberNode(str(reward))
    return EvaluationLink(PredicateNode("Reward"), rn)


def action_to_gym(action):
    """Map atomese actions to gym actions

    In CartPole-v1 the mapping is as follows

    SchemaNode("Go Right") -> 0
    SchemaNode("Go Left") -> 1

    """

    if SchemaNode("Go Right") == action:
        return 0
    if SchemaNode("Go Left") == action:
        return 1


def get_action(cs):
    """extract the action of a cognitive schematic.

    Given a cognitive schematic of that format

    PredictiveImplicationScope <tv>
      <vardecl>
      <expiry>
      And (or SimultaneousAnd?)
        <context>
        Execution
          <action>
          <input> [optional]
          <output> [optional]
      <goal>

    extract <action>. Ideally the input and output should also be
    extracted, for now only the action.

    """

    cnjs = cs.out[2].out
    execution = next(x for x in cnjs if x.type == types.ExecutionLink)
    return execution.out[0]


def make_goal(ci):
    """Define the goal of the current iteration.

    Here the goal of the current iteration is to have a reward of 1.

    Evaluation
      Predicate "Reward"
      Number 1

    """

    return reward_to_atomese(1)


def timestamp(atom, i, tv=None):
    """Timestamp a given atom.  Optionally set its TV

    AtTimeLink tv               # if tv is provided
      atom
      TimeNode str(i)

    """

    return AtTimeLink(atom, TimeNode(str(i)), tv=tv)


def plan(goal, expiry):
    """Plan the next actions given a goal and its expiry time offset

    Return a python list of cognivite schematics.  Whole cognitive
    schematics are output (instead of action plans) in order to make a
    decision based on their truth values.  Alternatively it could
    return a pair (action plan, tv), where tv has been evaluated to
    take into account the truth value of the context as well (which
    would differ from the truth value of rule in case the context is
    uncertain).

    The format for a cognitive schematic is as follows

    PredictiveImplicationScope <tv>
      <vardecl>
      <expiry>
      And (or SimultaneousAnd?)
        <context>
        Execution
          <action>
          <input> [optional]
          <output> [optional]
      <goal>

    """

    # For now we provide 2 hardwired rules
    #
    # 1. Push cart to the left (0) if angle is negative
    # 2. Push cart to the right (1) if angle is positive
    #
    # with some arbitrary truth value (stv 0.9, 0.1)
    angle = VariableNode("$angle")
    numt = TypeNode("NumberNode")
    time_offset = NumberNode(str(expiry))
    pole_angle = PredicateNode("Pole Angle")
    go_right = SchemaNode("Go Right")
    go_left = SchemaNode("Go Left")
    reward = PredicateNode("Reward")
    zero = NumberNode("0")
    unit = NumberNode("1")
    TV = TruthValue(0.9, 0.1)

    # PredictiveImplicationScope
    #   TypedVariable
    #     Variable "$angle"
    #     Type "NumberNode"
    #   Time "1"
    #   And (or SimultaneousAnd?)
    #     Evaluation
    #       Predicate "Pole Angle"
    #       Variable "$angle"
    #     GreaterThan
    #       Variable "$angle"
    #       0
    #     Execution
    #       Schema "Go Right"
    #   Evaluation
    #     Predicate "Reward"
    #     Number "1"
    cs_r = \
        PredictiveImplicationScopeLink(
            TypedVariableLink(angle, numt),
            time_offset,
            AndLink(
                # Context
                EvaluationLink(pole_angle, angle),
                GreaterThanLink(angle, zero),
                # Action
                ExecutionLink(go_right)),
            # Goal
            EvaluationLink(reward, unit),
            # TV
            tv=TV)

    # PredictiveImplicationScope
    #   TypedVariable
    #     Variable "$angle"
    #     Type "NumberNode"
    #   Time "1"
    #   And (or SimultaneousAnd?)
    #     Evaluation
    #       Predicate "Pole Angle"
    #       Variable "$angle"
    #     GreaterThan
    #       0
    #       Variable "$angle"
    #     Execution
    #       Schema "Go Left"
    #   Evaluation
    #     Predicate "Reward"
    #     Number "1"
    cs_l = \
        PredictiveImplicationScopeLink(
            TypedVariableLink(angle, numt),
            time_offset,
            AndLink(
                # Context
                EvaluationLink(pole_angle, angle),
                GreaterThanLink(zero, angle),
                # Action
                ExecutionLink(go_left)),
            # Goal
            EvaluationLink(reward, unit),
            # TV
            tv=TV)

    # Ideally we want to return only relevant cognitive schematics
    # (i.e. with contexts probabilistically currently true) for now
    # however we return everything and let to the decision progress
    # deal with it, as it should be able to.
    return [cs_l, cs_r]


def deduce(css):
    """Return an action distribution given a list cognitive schematics.

    The action distribution is actually a second order distribution,
    i.e. each action is weighted with a truth value.  Such truth
    value, called the action truth value, corresponds to the second
    order probability of acheiving the goal if the action is taken
    right now.

    Formally the meaning of the action truth value can be expressed as
    follows:

    Subset <action-tv>
      SubjectivePaths
        AtTime <now>
          Execute
            <action>
      SubjectivePaths
        AtTime <now + offset>
          <goal>

    where

    SubjectivePaths
      <P>

    is the set of all subjective paths compatible with <P>, and a
    subjective path is a sequence of sujective states (atomspace
    snapshots) indexed by time.

    In order to infer such action truth value one needs to perform
    deduction (or modus ponens) on the cognitive schematics, combining
    the probability of the context being currently true.

    """

    # For now assign a default TV to all actions.
    actions = {get_action(cs) for cs in css}
    tv = TruthValue(1, 0)
    return [(action, tv) for action in actions]


def decide(actdist):

    """Select the next action to enact from an action distribution.

    The action is selected from the action distribution, a list of
    pairs (action, tv), obtained from deduce.  The selection uses
    Thompson sampling leveraging the second order distribution to
    balance exploitation and exploration.

    """

    # Select the pair of action and its first order probability of
    # success according to Thompson sampling
    (action, pblty) = thompson_sample(actdist)

    # Return the action (we don't need the probability for now)
    return (action, pblty)


observation = env.reset()
cartpole_step_count =0
def cartpole_step():
    global observation
    global cartpole_step_count
    i = cartpole_step_count
    time.sleep(0.2)

    # Translate to atomese and timestamp observations
    atomese_obs = observation_to_atomese(observation)
    timestamped_obs = [timestamp(o, i, tv=TRUE_TV) for o in atomese_obs]
    print("timestamped_obs =", timestamped_obs)

    # Make the goal for that iteration
    goal = make_goal(i)
    print("goal =", goal)

    # Render the environment if X is running
    if X_ENABLED:
        env.render()

    # Plan, i.e. come up with cognitive schematics as plans.  Here the
    # goal expiry is 1, i.e. set for the next iteration.
    expiry = 1
    css = plan(goal, expiry)
    print("css =", css)

    # Deduce the action distribution
    actdist = deduce(css)
    print("actdist =", actdist)

    # Select the next action
    action, pblty = decide(actdist)
    print("(action, pblty) =", (action, pblty))

    # Convert atomese action to openai gym action
    gym_action = action_to_gym(action)
    print("gym_action =", gym_action)

    # Run the next step of the environment
    observation, reward, done, info = env.step(gym_action)
    print("observation =", observation)
    print("reward =", reward)
    print("info =", info)

    # Translate to atomese and timestamp reward
    timestamped_reward = timestamp(reward_to_atomese(reward), i, tv=TRUE_TV)
    print("timestamped_reward =", timestamped_reward)

    cartpole_step_count += 1
    if done:
        print("Stopping the openpsi loop")
        env.close()
        return TruthValue(0, 1)

    return TruthValue(1, 1)


cartpole_stepper = EvaluationLink(
    GroundedPredicateNode("py: cartpole_step"), ListLink()
)


cartpole_component = op.create_component("cartpole", cartpole_stepper)

########
# Main #
########
def main():
    op.run(cartpole_component)

# Launching main() from here produces a Segmentation fault
# if __name__ == "__main__":
#     main()
