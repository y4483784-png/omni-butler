---- MODULE UnknownWrite_TTrace_1794325508 ----
EXTENDS Sequences, UnknownWrite, TLCExt, Toolbox, Naturals, TLC

_expression ==
    LET UnknownWrite_TEExpression == INSTANCE UnknownWrite_TEExpression
    IN UnknownWrite_TEExpression!expression
----

_trace ==
    LET UnknownWrite_TETrace == INSTANCE UnknownWrite_TETrace
    IN UnknownWrite_TETrace!trace
----

_inv ==
    ~(
        TLCGet("level") = Len(_TETrace)
        /\
        stopped = (TRUE)
        /\
        armed = (FALSE)
        /\
        claim = ("open")
        /\
        blindPosts = (0)
        /\
        badExec = (TRUE)
        /\
        remoteCount = (0)
    )
----

_init ==
    /\ badExec = _TETrace[1].badExec
    /\ claim = _TETrace[1].claim
    /\ remoteCount = _TETrace[1].remoteCount
    /\ armed = _TETrace[1].armed
    /\ blindPosts = _TETrace[1].blindPosts
    /\ stopped = _TETrace[1].stopped
----

_next ==
    /\ \E i,j \in DOMAIN _TETrace:
        /\ \/ /\ j = i + 1
              /\ i = TLCGet("level")
        /\ badExec  = _TETrace[i].badExec
        /\ badExec' = _TETrace[j].badExec
        /\ claim  = _TETrace[i].claim
        /\ claim' = _TETrace[j].claim
        /\ remoteCount  = _TETrace[i].remoteCount
        /\ remoteCount' = _TETrace[j].remoteCount
        /\ armed  = _TETrace[i].armed
        /\ armed' = _TETrace[j].armed
        /\ blindPosts  = _TETrace[i].blindPosts
        /\ blindPosts' = _TETrace[j].blindPosts
        /\ stopped  = _TETrace[i].stopped
        /\ stopped' = _TETrace[j].stopped

\* Uncomment the ASSUME below to write the states of the error trace
\* to the given file in Json format. Note that you can pass any tuple
\* to `JsonSerialize`. For example, a sub-sequence of _TETrace.
    \* ASSUME
    \*     LET J == INSTANCE Json
    \*         IN J!JsonSerialize("UnknownWrite_TTrace_1794325508.json", _TETrace)

=============================================================================

 Note that you can extract this module `UnknownWrite_TEExpression`
  to a dedicated file to reuse `expression` (the module in the 
  dedicated `UnknownWrite_TEExpression.tla` file takes precedence 
  over the module `UnknownWrite_TEExpression` below).

---- MODULE UnknownWrite_TEExpression ----
EXTENDS Sequences, UnknownWrite, TLCExt, Toolbox, Naturals, TLC

expression == 
    [
        \* To hide variables of the `UnknownWrite` spec from the error trace,
        \* remove the variables below.  The trace will be written in the order
        \* of the fields of this record.
        badExec |-> badExec
        ,claim |-> claim
        ,remoteCount |-> remoteCount
        ,armed |-> armed
        ,blindPosts |-> blindPosts
        ,stopped |-> stopped
        
        \* Put additional constant-, state-, and action-level expressions here:
        \* ,_stateNumber |-> _TEPosition
        \* ,_badExecUnchanged |-> badExec = badExec'
        
        \* Format the `badExec` variable as Json value.
        \* ,_badExecJson |->
        \*     LET J == INSTANCE Json
        \*     IN J!ToJson(badExec)
        
        \* Lastly, you may build expressions over arbitrary sets of states by
        \* leveraging the _TETrace operator.  For example, this is how to
        \* count the number of times a spec variable changed up to the current
        \* state in the trace.
        \* ,_badExecModCount |->
        \*     LET F[s \in DOMAIN _TETrace] ==
        \*         IF s = 1 THEN 0
        \*         ELSE IF _TETrace[s].badExec # _TETrace[s-1].badExec
        \*             THEN 1 + F[s-1] ELSE F[s-1]
        \*     IN F[_TEPosition - 1]
    ]

=============================================================================



Parsing and semantic processing can take forever if the trace below is long.
 In this case, it is advised to uncomment the module below to deserialize the
 trace from a generated binary file.

\*
\*---- MODULE UnknownWrite_TETrace ----
\*EXTENDS IOUtils, UnknownWrite, TLC
\*
\*trace == IODeserialize("UnknownWrite_TTrace_1794325508.bin", TRUE)
\*
\*=============================================================================
\*

---- MODULE UnknownWrite_TETrace ----
EXTENDS UnknownWrite, TLC

trace == 
    <<
    ([stopped |-> FALSE,armed |-> FALSE,claim |-> "open",blindPosts |-> 0,badExec |-> FALSE,remoteCount |-> 0]),
    ([stopped |-> TRUE,armed |-> FALSE,claim |-> "open",blindPosts |-> 0,badExec |-> FALSE,remoteCount |-> 0]),
    ([stopped |-> TRUE,armed |-> FALSE,claim |-> "open",blindPosts |-> 0,badExec |-> TRUE,remoteCount |-> 0])
    >>
----


=============================================================================

---- CONFIG UnknownWrite_TTrace_1794325508 ----
CONSTANTS
    breakBlindRetry = FALSE
    breakStoppedExecute = TRUE

INVARIANT
    _inv

CHECK_DEADLOCK
    \* CHECK_DEADLOCK off because of PROPERTY or INVARIANT above.
    FALSE

INIT
    _init

NEXT
    _next

CONSTANT
    _TETrace <- _trace

ALIAS
    _expression
=============================================================================
\* Generated on Tue Nov 10 15:45:08 UTC 2026