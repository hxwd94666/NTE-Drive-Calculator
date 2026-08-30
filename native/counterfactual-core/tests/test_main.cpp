#include "counterfactual/engine.hpp"
#include "counterfactual/json.hpp"
#include "counterfactual/model.hpp"

#include <cmath>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>

namespace {

int failures{};

void check(bool condition, const std::string& message) {
    if (!condition) {
        ++failures;
        std::cerr << "FAIL: " << message << '\n';
    }
}

void close(double actual, double expected, const std::string& message) {
    check(std::abs(actual - expected) <= 1e-9 * std::max({1.0, std::abs(actual), std::abs(expected)}), message);
}

counterfactual::Request fixture() {
    const std::string path = std::string(COUNTERFACTUAL_FIXTURE_DIR) + "/ordinary-buffs.request.json";
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("cannot load fixture: " + path);
    std::ostringstream content;
    content << input.rdbuf();
    return counterfactual::request_from_json(counterfactual::json::parse(content.str()));
}

void test_fixture_statuses_and_values() {
    const auto response = counterfactual::calculate(fixture());
    check(response.results.size() == 8, "all Buff groups are retained in input order");
    check(response.results[0].status == "complete", "ordinary damage Buff is complete");
    check(response.results[1].status == "partial", "mixed supported and target-sensitive Buff is partial");
    check(response.results[2].status == "unavailable", "target-sensitive Buff without profile is unavailable");
    check(response.results[3].status == "not_applicable", "out-of-range Buff is not applicable");
    check(response.results[4].status == "complete", "frozen critical branch Buff is complete");
    check(response.results[5].status == "complete", "Cosmos target resistance is quantified");
    check(response.results[6].status == "unavailable", "psychically DefIgnore is not consumed");
    check(response.results[7].status == "complete", "ordinary DefIgnore is quantified");
    close(*response.results[0].hits[0].quantified_ratio, 0.8846153846153845, "damage-up ratio");
    close(*response.results[4].hits[0].quantified_ratio, 1.0, "non-critical branch ignores crit damage");
    close(*response.results[4].hits[1].quantified_ratio, 5.0 / 6.0, "critical branch remains critical");
    check(response.results[2].hits[2].status == "not_applicable", "same wire target in another half is isolated");
    check(response.results[2].hits[3].status == "not_applicable", "different target in the same half is isolated");
    check(!response.results[2].hits[0].quantified_ratio, "unavailable never exposes ratio=1");
    check(response.results[3].hits[0].quantified_ratio == 1.0, "not_applicable exposes exact ratio=1");
    check(response.results[5].hits[4].status == "complete", "Cosmos property matches case-insensitively");
    check(!response.results[6].hits[5].quantified_ratio, "psychically DefIgnore exposes no false ratio");
    check(response.results[6].hits[5].gap_codes == std::vector<std::string>{"formula_family_unsupported"},
          "psychically DefIgnore matches the Python projection gate");
    close(*response.results[7].hits[6].quantified_ratio, 0.9741935483870968,
          "DefBase uses the production divide-by-six normalization");
}

void test_half_open_interval_boundary() {
    auto request = fixture();
    request.buffs.resize(1);
    request.buffs[0].intervals[0].end_us = request.hits[0].relative_time_us;
    const auto response = counterfactual::calculate(request);
    check(response.results[0].hits[0].status == "not_applicable", "end_us is excluded");
}

void test_incomplete_axis_adds_gap() {
    auto request = fixture();
    request.buffs.resize(1);
    request.axis.complete = false;
    const auto response = counterfactual::calculate(request);
    check(response.results[0].status == "partial", "incomplete axis downgrades quantified result");
    check(response.results[0].gap_codes == std::vector<std::string>{"final_axis_incomplete"}, "incomplete-axis gap is stable");
}

void test_duplicate_event_rejected() {
    auto request = fixture();
    request.hits[1].event_id = request.hits[0].event_id;
    bool rejected = false;
    try {
        counterfactual::validate_request(request);
    } catch (const std::exception&) {
        rejected = true;
    }
    check(rejected, "duplicate event_id is rejected");
}

void test_invalid_interval_rejected() {
    auto request = fixture();
    request.buffs[0].intervals[0].end_us = request.buffs[0].intervals[0].start_us;
    bool rejected = false;
    try {
        counterfactual::validate_request(request);
    } catch (const std::exception&) {
        rejected = true;
    }
    check(rejected, "empty interval is rejected");
}

void test_unsupported_mechanic_is_unavailable() {
    auto request = fixture();
    request.buffs.resize(1);
    request.hits.resize(1);
    request.hits[0].classification = "dot";
    const auto response = counterfactual::calculate(request);
    check(response.results[0].hits[0].status == "unavailable", "unsupported DOT remains unavailable");
    check(!response.results[0].hits[0].quantified_ratio, "unsupported DOT has no ratio");
}

void test_unknown_team_others_beneficiary_is_unavailable() {
    auto request = fixture();
    request.buffs.resize(1);
    request.hits.resize(1);
    request.hits[0].character_id = std::nullopt;
    request.buffs[0].intervals[0].target_scope = "team_others";
    const auto response = counterfactual::calculate(request);
    check(response.results[0].hits[0].status == "unavailable", "unknown team_others recipient is unavailable");
    check(response.results[0].hits[0].gap_codes == std::vector<std::string>({
        "scaling_dependency_unresolved", "team_others_beneficiary_unknown"
    }), "unknown recipient preserves stable gaps");
}

void check_unavailable_without_projection(
    const counterfactual::HitResult& result,
    const std::string& message
) {
    check(result.status == "unavailable", message + " is unavailable");
    check(!result.quantified_ratio, message + " has no ratio");
    check(!result.candidate_damage, message + " has no candidate damage");
    check(result.gap_codes == std::vector<std::string>{"formula_family_unsupported"},
          message + " preserves the unsupported-formula gap");
}

void test_target_modifier_in_character_scope_is_unavailable() {
    auto request = fixture();
    request.buffs.resize(1);
    request.hits.resize(1);
    request.buffs[0].intervals[0].modifiers[0].property_id = "DamageResistChaosBase";
    const auto response = counterfactual::calculate(request);
    check_unavailable_without_projection(
        response.results[0].hits[0], "target modifier in self scope");
}

void test_character_modifier_in_target_scope_is_unavailable() {
    auto request = fixture();
    request.buffs.resize(1);
    request.hits.resize(1);
    request.buffs[0].intervals[0].target_scope = "target";
    request.buffs[0].intervals[0].target_id = request.hits[0].target_id;
    const auto response = counterfactual::calculate(request);
    check_unavailable_without_projection(
        response.results[0].hits[0], "character modifier in target scope");
}

void test_zero_damage_increase_baseline_is_unavailable() {
    auto request = fixture();
    request.buffs.resize(1);
    request.hits.resize(1);
    request.characters[0].stats["DamageUpGeneralBase"] = -1.25;
    const auto response = counterfactual::calculate(request);
    const auto& result = response.results[0].hits[0];
    check(result.status == "unavailable", "zero damage-increase baseline is unavailable");
    check(result.gap_codes == std::vector<std::string>{"damage_increase_dependency_unresolved"},
          "zero damage-increase baseline uses the Python gap code");
    check(!result.quantified_ratio, "zero damage-increase baseline has no ratio");
    check(!result.candidate_damage, "zero damage-increase baseline has no candidate damage");
}

void test_zero_resistance_factor_is_unavailable() {
    auto request = fixture();
    request.buffs = {request.buffs[2]};
    request.hits.resize(1);
    request.target_profiles.push_back(counterfactual::TargetProfile{
        request.hits[0].scope_half,
        request.hits[0].target_id,
        request.hits[0].damage_attribute,
        1.20,
        std::nullopt,
    });
    const auto response = counterfactual::calculate(request);
    const auto& result = response.results[0].hits[0];
    check(result.status == "unavailable", "zero resistance factor is unavailable");
    check(result.gap_codes == std::vector<std::string>{"target_resistance_dependency_changed"},
          "zero resistance factor uses the Python gap code");
    check(!result.quantified_ratio, "zero resistance factor has no ratio");
    check(!result.candidate_damage, "zero resistance factor has no candidate damage");
}

void test_non_finite_formula_inputs_are_rejected() {
    const auto infinity = std::numeric_limits<double>::infinity();
    const auto nan = std::numeric_limits<double>::quiet_NaN();

    auto resistance = fixture();
    resistance.target_profiles.push_back(counterfactual::TargetProfile{
        "upper", "shared-wire-id", "chaos", infinity, std::nullopt});
    bool rejected_resistance = false;
    try { counterfactual::validate_request(resistance); }
    catch (const std::exception&) { rejected_resistance = true; }
    check(rejected_resistance, "non-finite target resistance is rejected");

    auto defense = fixture();
    defense.target_profiles.push_back(counterfactual::TargetProfile{
        "upper", "shared-wire-id", "chaos", 0.20, infinity});
    bool rejected_defense = false;
    try { counterfactual::validate_request(defense); }
    catch (const std::exception&) { rejected_defense = true; }
    check(rejected_defense, "non-finite enemy defense is rejected");

    auto critical_rate = fixture();
    critical_rate.hits[0].critical_rate = nan;
    bool rejected_critical_rate = false;
    try { counterfactual::validate_request(critical_rate); }
    catch (const std::exception&) { rejected_critical_rate = true; }
    check(rejected_critical_rate, "non-finite critical rate is rejected");
}

void test_duplicate_target_profile_is_rejected() {
    auto request = fixture();
    request.target_profiles.push_back(counterfactual::TargetProfile{
        "upper", "shared-wire-id", "chaos", 0.20, std::nullopt});
    request.target_profiles.push_back(counterfactual::TargetProfile{
        "upper", "shared-wire-id", "chaos", 0.30, 100.0});
    bool rejected = false;
    try { counterfactual::validate_request(request); }
    catch (const std::exception&) { rejected = true; }
    check(rejected, "duplicate scope_half/target_id/damage_attribute profile is rejected");
}

void test_target_profiles_allow_multiple_attributes_for_one_target() {
    auto request = fixture();
    request.target_profiles.push_back(counterfactual::TargetProfile{
        "upper", "shared-wire-id", "chaos", 0.20, std::nullopt});
    request.target_profiles.push_back(counterfactual::TargetProfile{
        "upper", "shared-wire-id", "cosmos", 0.30, 100.0});
    bool rejected = false;
    try { counterfactual::validate_request(request); }
    catch (const std::exception&) { rejected = true; }
    check(!rejected, "one target can retain separate attribute profiles");
}

void test_target_profile_attribute_must_match_hit() {
    auto request = fixture();
    request.buffs = {request.buffs[5]};
    request.hits = {request.hits[4]};
    request.target_profiles[0].damage_attribute = "chaos";
    const auto response = counterfactual::calculate(request);
    const auto& result = response.results[0].hits[0];
    check(result.status == "unavailable", "mismatched target profile attribute is unavailable");
    check(result.gap_codes == std::vector<std::string>{"target_resistance_dependency_changed"},
          "mismatched attribute preserves the target-resistance gap");
}

}  // namespace

int main() {
    try {
        test_fixture_statuses_and_values();
        test_half_open_interval_boundary();
        test_incomplete_axis_adds_gap();
        test_duplicate_event_rejected();
        test_invalid_interval_rejected();
        test_unsupported_mechanic_is_unavailable();
        test_unknown_team_others_beneficiary_is_unavailable();
        test_target_modifier_in_character_scope_is_unavailable();
        test_character_modifier_in_target_scope_is_unavailable();
        test_zero_damage_increase_baseline_is_unavailable();
        test_zero_resistance_factor_is_unavailable();
        test_non_finite_formula_inputs_are_rejected();
        test_duplicate_target_profile_is_rejected();
        test_target_profiles_allow_multiple_attributes_for_one_target();
        test_target_profile_attribute_must_match_hit();
    } catch (const std::exception& error) {
        std::cerr << "unexpected exception: " << error.what() << '\n';
        return 2;
    }
    if (failures != 0) {
        std::cerr << failures << " test(s) failed\n";
        return 1;
    }
    std::cout << "counterfactual-core tests passed\n";
    return 0;
}
