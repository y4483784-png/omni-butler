---- MODULE ErrandEcho ----
EXTENDS Integers

CONSTANT breakEmptyEcho, breakLookupPlace, breakSecrets

VARIABLES claim, echo, order, places, record, looked, published

vars == <<claim, echo, order, places, record, looked, published>>

Claims == {"open", "unknown", "written"}
Echoes == {"absent", "match"}
Fields == {"outcome", "order_id", "told", "clicks", "idempotency_key", "pay", "login"}
Sealed == {"pay", "login"}

Init ==
  /\ claim = "open"
  /\ echo = "absent"
  /\ order = FALSE
  /\ places = 0
  /\ record = FALSE
  /\ looked = FALSE
  /\ published = {}

Place ==
  /\ claim = "open"
  /\ places = 0
  /\ places' = 1
  /\ claim' = "unknown"
  /\ echo' \in Echoes
  /\ order' \in BOOLEAN
  /\ record' = FALSE
  /\ looked' = FALSE
  /\ published' = {}

Store ==
  /\ claim = "unknown"
  /\ ~record
  /\ echo = "match"
  /\ order
  /\ record' = TRUE
  /\ claim' = "written"
  /\ UNCHANGED <<echo, order, places, looked, published>>

StoreEmpty ==
  /\ breakEmptyEcho
  /\ claim = "unknown"
  /\ ~record
  /\ echo = "absent"
  /\ record' = TRUE
  /\ claim' = "written"
  /\ UNCHANGED <<echo, order, places, looked, published>>

Lookup ==
  /\ claim = "unknown"
  /\ ~record
  /\ ~looked
  /\ looked' = TRUE
  /\ places' = IF breakLookupPlace THEN places + 1 ELSE places
  /\ UNCHANGED <<claim, echo, order, record, published>>

Publish ==
  /\ places = 1
  /\ published = {}
  /\ published' = IF breakSecrets
                  THEN {"outcome", "pay", "login"}
                  ELSE {"outcome", "order_id", "told", "clicks", "idempotency_key"}
  /\ UNCHANGED <<claim, echo, order, places, record, looked>>

Done ==
  /\ (record \/ looked \/ published /= {})
  /\ UNCHANGED vars

Next ==
  \/ Place
  \/ Store
  \/ StoreEmpty
  \/ Lookup
  \/ Publish
  \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ claim \in Claims
  /\ echo \in Echoes
  /\ order \in BOOLEAN
  /\ places \in 0..2
  /\ record \in BOOLEAN
  /\ looked \in BOOLEAN
  /\ published \subseteq Fields

EchoRequiredForPlaced == record => echo = "match" /\ order

LookupDoesNotPlace == looked => places = 1

SecretsDropped == published \intersect Sealed = {}

====
