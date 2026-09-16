# NTE Core build variant

The executable here is identical to the Core in the native-capture component bundle.
The authoritative build options, source revision, input digest and validation boundaries are recorded in
[the Core source declaration](../native-capture/core/SOURCE.md) and
[the paired component manifest](../native-capture/component-bundle.json).

The native and packet entries preserve separate sources. Common observation envelopes retain unknown
payloads; calculation interpretation belongs to the independent analysis component.
Updating files does not update an already running game process. See the component manifest for the paired runtime.
