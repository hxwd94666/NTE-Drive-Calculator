#include "counterfactual/engine.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <set>
#include <stdexcept>

namespace counterfactual {
namespace {

using Stats = std::map<std::string, double, std::less<>>;

const std::map<std::string, std::string, std::less<>> element_properties{
    {"chaos", "DamageUpChaosBase"}, {"cosmos", "DamageUpCosmosBase"},
    {"incantation", "DamageUpIncantationBase"}, {"lakshana", "DamageUpLakshanaBase"},
    {"nature", "DamageUpNatureBase"}, {"psyche", "DamageUpPsycheBase"},
    {"psychically", "DamageUpPsychicallyBase"},
};
const std::map<std::string, std::string, std::less<>> penetration_properties{
    {"chaos", "DamagePenetrateChaos"}, {"cosmos", "DamagePenetrateCosmos"},
    {"incantation", "DamagePenetrateIncantation"}, {"lakshana", "DamagePenetrateLakshana"},
    {"nature", "DamagePenetrateNature"}, {"psyche", "DamagePenetratePsyche"},
    {"psychically", "DamagePenetratePsychically"},
};

double get(const Stats& values, const std::string& key, double fallback = 0.0) {
    const auto found = values.find(key);
    return found == values.end() ? fallback : found->second;
}

std::string ascii_lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char character) {
        return static_cast<char>(std::tolower(character));
    });
    return value;
}

bool interval_applies(const Interval& interval, const Hit& hit) {
    if (!(interval.start_us <= hit.relative_time_us && hit.relative_time_us < interval.end_us)) return false;
    if (!interval.scope_half.empty() && interval.scope_half != hit.scope_half) return false;
    if (interval.target_scope == "team") return true;
    if (interval.target_scope == "team_others") return !hit.character_id || *hit.character_id != interval.source_character_id;
    if (interval.target_scope == "self") return hit.character_id && *hit.character_id == interval.source_character_id;
    if (interval.target_scope.rfind("character:", 0) == 0) {
        return hit.character_id && interval.target_scope == "character:" + std::to_string(*hit.character_id);
    }
    if (interval.target_scope == "target") return interval.target_id == hit.target_id;
    return false;
}

bool is_target_property(const std::string& property) {
    return property.rfind("DamageResist", 0) == 0;
}

bool supported_character_property(const std::string& property) {
    static const std::set<std::string> supported{
        "AtkUp", "AtkAdd", "HPMaxUp", "HPMaxAdd", "DefUp", "DefAdd",
        "CritBase", "CritDamageBase", "DamageUpGeneralBase", "DefIgnore",
        "DamagePenetrateChaos", "DamagePenetrateCosmos", "DamagePenetrateIncantation",
        "DamagePenetrateLakshana", "DamagePenetrateNature", "DamagePenetratePsyche",
        "DamagePenetratePsychically",
        "DamageUpChaosBase", "DamageUpCosmosBase", "DamageUpIncantationBase",
        "DamageUpLakshanaBase", "DamageUpNatureBase", "DamageUpPsycheBase",
        "DamageUpPsychicallyBase",
    };
    return supported.contains(property);
}

struct Projection {
    Stats character;
    double target_resistance_delta{};
    std::vector<std::string> gaps;
};

Projection project(const Request& request, const Hit& hit, const BuffGroup* removed) {
    Projection projection;
    if (!hit.character_id) return projection;
    const auto character = std::find_if(request.characters.begin(), request.characters.end(),
        [&](const Character& row) { return row.character_id == *hit.character_id; });
    if (character == request.characters.end()) return projection;
    projection.character = character->stats;
    for (const auto& buff : request.buffs) {
        if (&buff == removed) continue;
        for (const auto& interval : buff.intervals) {
            if (!interval_applies(interval, hit)) continue;
            for (const auto& modifier : interval.modifiers) {
                if (modifier.operation != "additive" || !modifier.value ||
                    modifier.value_confidence == "low" || !modifier.calculation_asset_path.empty()) {
                    projection.gaps.push_back("formula_family_unsupported");
                    continue;
                }
                if (is_target_property(modifier.property_id)) {
                    if (interval.target_scope != "target") projection.gaps.push_back("formula_family_unsupported");
                    else if (ascii_lower(modifier.property_id).find(
                                 ascii_lower(hit.damage_attribute)) != std::string::npos) {
                        projection.target_resistance_delta += *modifier.value;
                    }
                    continue;
                }
                if (interval.target_scope == "target" || !supported_character_property(modifier.property_id)) {
                    projection.gaps.push_back("formula_family_unsupported");
                    continue;
                }
                projection.character[modifier.property_id] += *modifier.value;
            }
        }
    }
    std::sort(projection.gaps.begin(), projection.gaps.end());
    projection.gaps.erase(std::unique(projection.gaps.begin(), projection.gaps.end()), projection.gaps.end());
    return projection;
}

std::vector<std::string> group_projection_gaps(const BuffGroup& group, const Hit& hit) {
    std::vector<std::string> gaps;
    for (const auto& interval : group.intervals) {
        if (!interval_applies(interval, hit)) continue;
        if (interval.target_scope == "team_others" && !hit.character_id) {
            gaps.push_back("team_others_beneficiary_unknown");
        }
        if (interval.modifiers.empty()) gaps.push_back("formula_family_unsupported");
        for (const auto& modifier : interval.modifiers) {
            const bool target_property = is_target_property(modifier.property_id);
            const bool scope_mismatch =
                (target_property && interval.target_scope != "target") ||
                (!target_property && supported_character_property(modifier.property_id) &&
                 interval.target_scope == "target");
            if (modifier.operation != "additive" || !modifier.value ||
                modifier.value_confidence == "low" || !modifier.calculation_asset_path.empty() ||
                (!target_property && !supported_character_property(modifier.property_id)) ||
                scope_mismatch) {
                gaps.push_back("formula_family_unsupported");
            }
        }
    }
    std::sort(gaps.begin(), gaps.end());
    gaps.erase(std::unique(gaps.begin(), gaps.end()), gaps.end());
    return gaps;
}

bool changed(const Stats& original, const Stats& candidate, const std::string& key) {
    return std::abs(get(original, key) - get(candidate, key)) > 1e-12;
}

void add_gap(std::vector<std::string>& gaps, const std::string& gap) {
    if (std::find(gaps.begin(), gaps.end(), gap) == gaps.end()) gaps.push_back(gap);
}

std::optional<double> safe_ratio(double candidate, double original) {
    if (!std::isfinite(candidate) || !std::isfinite(original) ||
        original <= 0.0 || candidate < 0.0) {
        return std::nullopt;
    }
    const double ratio = candidate / original;
    return std::isfinite(ratio) && ratio >= 0.0
        ? std::optional<double>{ratio}
        : std::nullopt;
}

double resistance_multiplier(double resistance) {
    return resistance >= 0.0 ? 1.0 - resistance : 1.0 - resistance / 1.10;
}

const TargetProfile* target_profile(const Request& request, const Hit& hit) {
    const auto found = std::find_if(request.target_profiles.begin(), request.target_profiles.end(),
        [&](const TargetProfile& row) {
            return row.scope_half == hit.scope_half && row.target_id == hit.target_id &&
                   row.damage_attribute == hit.damage_attribute;
        });
    return found == request.target_profiles.end() ? nullptr : &*found;
}

HitResult compare_hit(const Request& request, const BuffGroup& group, const Hit& hit) {
    HitResult result;
    result.event_id = hit.event_id;
    const bool active = std::any_of(group.intervals.begin(), group.intervals.end(),
        [&](const Interval& interval) { return interval_applies(interval, hit); });
    if (!active || hit.direction != "outgoing" || hit.relative_time_us < request.axis.range_start_us ||
        hit.relative_time_us >= request.axis.range_end_us) {
        result.status = "not_applicable";
        result.quantified_ratio = 1.0;
        result.candidate_damage = hit.damage;
        return result;
    }
    if (hit.classification != "direct") {
        result.status = "unavailable";
        result.gap_codes = {"formula_family_unsupported"};
        return result;
    }
    const auto original = project(request, hit, nullptr);
    const auto candidate = project(request, hit, &group);
    std::vector<std::string> gaps = group_projection_gaps(group, hit);
    std::vector<std::string> included;
    double ratio = 1.0;
    if (original.character.empty() || candidate.character.empty()) {
        add_gap(gaps, "scaling_dependency_unresolved");
    }

    const std::map<std::string, std::vector<std::string>, std::less<>> scaling{
        {"Atk", {"AtkBase", "AtkUp", "AtkAdd"}},
        {"HPMax", {"HPMaxBase", "HPMaxUp", "HPMaxAdd"}},
        {"Def", {"DefBase", "DefUp", "DefAdd"}},
    };
    const auto scaling_row = scaling.find(hit.scaling_property_id);
    bool scaling_change = false;
    if (scaling_row != scaling.end()) {
        for (const auto& property : scaling_row->second) scaling_change |= changed(original.character, candidate.character, property);
        if (scaling_change) {
            const auto& keys = scaling_row->second;
            const double before = get(original.character, keys[0]) * (1.0 + get(original.character, keys[1])) + get(original.character, keys[2]);
            const double after = get(candidate.character, keys[0]) * (1.0 + get(candidate.character, keys[1])) + get(candidate.character, keys[2]);
            const auto scaling_ratio = safe_ratio(after, before);
            if (scaling_ratio) {
                ratio *= *scaling_ratio;
                included.push_back("scaling");
            } else {
                add_gap(gaps, "scaling_dependency_unresolved");
            }
        }
    } else {
        for (const auto& [_, keys] : scaling) for (const auto& key : keys) scaling_change |= changed(original.character, candidate.character, key);
        if (scaling_change) add_gap(gaps, "scaling_dependency_unresolved");
    }

    if (changed(original.character, candidate.character, "CritBase") ||
        changed(original.character, candidate.character, "CritDamageBase")) {
        std::optional<double> critical_ratio;
        if (hit.critical_policy == "disabled") {
            critical_ratio = 1.0;
        } else if (hit.critical_policy == "character" ||
                   (hit.critical_policy == "fixed" && hit.critical_rate.has_value())) {
            const bool fixed = hit.critical_policy == "fixed";
            const double original_rate = fixed ? *hit.critical_rate : get(original.character, "CritBase", 0.05);
            const double candidate_rate = fixed ? *hit.critical_rate : get(candidate.character, "CritBase", 0.05);
            const double before = 1.0 + std::clamp(original_rate, 0.0, 1.0) * std::max(0.0, get(original.character, "CritDamageBase", 0.5));
            const double after = 1.0 + std::clamp(candidate_rate, 0.0, 1.0) * std::max(0.0, get(candidate.character, "CritDamageBase", 0.5));
            critical_ratio = safe_ratio(after, before);
        }
        if (critical_ratio) { ratio *= *critical_ratio; included.push_back("critical"); }
        else add_gap(gaps, "critical_policy_unknown");
    }

    const auto element = element_properties.find(hit.damage_attribute);
    const std::string element_property = element == element_properties.end() ? "" : element->second;
    if (changed(original.character, candidate.character, "DamageUpGeneralBase") ||
        (!element_property.empty() && changed(original.character, candidate.character, element_property))) {
        const double before = std::max(0.0, 1.0 + get(original.character, "DamageUpGeneralBase") + get(original.character, element_property));
        const double after = std::max(0.0, 1.0 + get(candidate.character, "DamageUpGeneralBase") + get(candidate.character, element_property));
        const auto increase_ratio = safe_ratio(after, before);
        if (increase_ratio) {
            ratio *= *increase_ratio;
            included.push_back("damage_increase");
        } else {
            add_gap(gaps, "damage_increase_dependency_unresolved");
        }
    }

    if (changed(original.character, candidate.character, "DefIgnore")) {
        if (hit.damage_attribute == "psychically") {
            // Python cancels this defense branch, while its projection gate keeps the
            // changed modifier explicitly unavailable until the slice models it.
            add_gap(gaps, "formula_family_unsupported");
        } else {
            const auto* profile = target_profile(request, hit);
            if (!profile || !profile->enemy_defense_base) add_gap(gaps, "target_defense_dependency_changed");
            else {
                const double level = static_cast<double>(std::find_if(
                    request.characters.begin(), request.characters.end(),
                    [&](const Character& row) {
                        return hit.character_id && row.character_id == *hit.character_id;
                    })->character_level);
                const auto factor = [&](const Stats& values) {
                    const double defense = (*profile->enemy_defense_base / 6.0) *
                        (1.0 - std::clamp(get(values, "DefIgnore"), -1.0, 1.0));
                    return (level + 100.0) / (defense + level + 100.0);
                };
                const auto defense_ratio = safe_ratio(
                    factor(candidate.character), factor(original.character));
                if (defense_ratio) {
                    ratio *= *defense_ratio;
                    included.push_back("target_defense");
                } else {
                    add_gap(gaps, "target_defense_dependency_changed");
                }
            }
        }
    }

    const auto penetration = penetration_properties.find(hit.damage_attribute);
    const std::string penetration_property = penetration == penetration_properties.end() ? "" : penetration->second;
    if ((!penetration_property.empty() && changed(original.character, candidate.character, penetration_property)) ||
        std::abs(original.target_resistance_delta - candidate.target_resistance_delta) > 1e-12) {
        const auto* profile = target_profile(request, hit);
        if (!profile) add_gap(gaps, "target_resistance_dependency_changed");
        else {
            const double before_resistance = profile->resistance + original.target_resistance_delta - get(original.character, penetration_property);
            const double after_resistance = profile->resistance + candidate.target_resistance_delta - get(candidate.character, penetration_property);
            const auto resistance_ratio = safe_ratio(
                std::max(0.0, resistance_multiplier(after_resistance)),
                std::max(0.0, resistance_multiplier(before_resistance)));
            if (resistance_ratio) {
                ratio *= *resistance_ratio;
                included.push_back("target_resistance");
            } else {
                add_gap(gaps, "target_resistance_dependency_changed");
            }
        }
    }

    if (!request.axis.complete && !included.empty()) add_gap(gaps, "final_axis_incomplete");
    std::sort(gaps.begin(), gaps.end());
    result.gap_codes = gaps;
    if (!gaps.empty() && included.empty()) result.status = "unavailable";
    else if (!gaps.empty()) result.status = "partial";
    else if (included.empty()) {
        result.status = "not_applicable";
        result.quantified_ratio = 1.0;
        result.candidate_damage = hit.damage;
        return result;
    } else result.status = "complete";
    if (result.status != "unavailable") {
        const double candidate_damage = hit.damage * ratio;
        if (!std::isfinite(ratio) || !std::isfinite(candidate_damage)) {
            add_gap(result.gap_codes, "formula_family_unsupported");
            std::sort(result.gap_codes.begin(), result.gap_codes.end());
            result.status = "unavailable";
            return result;
        }
        result.quantified_ratio = ratio;
        result.candidate_damage = candidate_damage;
    }
    return result;
}

BuffResult aggregate(const Request& request, const BuffGroup& group) {
    BuffResult result;
    result.buff_key = group.buff_key;
    double increment{};
    std::set<std::string> gaps;
    for (const auto& hit : request.hits) {
        auto row = compare_hit(request, group, hit);
        result.basis_damage += hit.damage;
        if (row.status == "complete") result.fully_quantified_damage += hit.damage;
        else if (row.status == "partial") result.partially_quantified_damage += hit.damage;
        else if (row.status == "unavailable") result.unavailable_damage += hit.damage;
        else result.proven_unchanged_damage += hit.damage;
        if (row.candidate_damage && (row.status == "complete" || row.status == "partial")) increment += hit.damage - *row.candidate_damage;
        gaps.insert(row.gap_codes.begin(), row.gap_codes.end());
        result.hits.push_back(std::move(row));
    }
    result.gap_codes.assign(gaps.begin(), gaps.end());
    const double quantified = result.fully_quantified_damage + result.partially_quantified_damage;
    if (result.unavailable_damage > 0.0 && quantified <= 0.0) result.status = "unavailable";
    else if (result.partially_quantified_damage > 0.0 || result.unavailable_damage > 0.0) result.status = "partial";
    else if (result.fully_quantified_damage > 0.0) result.status = "complete";
    else result.status = "not_applicable";
    if (result.status == "unavailable") result.quantified_increment = std::nullopt;
    else result.quantified_increment = result.status == "not_applicable" ? 0.0 : increment;
    return result;
}

}  // namespace

Response calculate(const Request& request) {
    validate_request(request);
    Response response;
    response.model_version = request.model_version;
    response.dataset_version = request.dataset_version;
    for (const auto& buff : request.buffs) response.results.push_back(aggregate(request, buff));
    return response;
}

}  // namespace counterfactual
